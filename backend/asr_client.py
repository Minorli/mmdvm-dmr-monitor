"""Bounded JSON IPC with a disposable speech-model subprocess."""
import asyncio
import contextlib
import json
import math
import os
import sys
from pathlib import Path
from uuid import uuid4

from backend.config import ROOT

MAX_MESSAGE = 65536
ENVIRONMENT_NAMES = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP",
    "LD_LIBRARY_PATH", "SSL_CERT_FILE", "SSL_CERT_DIR", "SYSTEMROOT", "WINDIR",
)


class AsrError(RuntimeError):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class AsrClient:
    def __init__(self, settings, command=None, load_timeout: float = 600, job_timeout: float | None = None):
        self.settings = settings
        self.command = command or [sys.executable, "-u", "-m", "backend.asr_worker"]
        self.load_timeout = load_timeout
        self.job_timeout = job_timeout or settings.job_lease_seconds * .8
        self.process = None
        self.ready = False
        self.stderr_bytes = 0
        self._stderr_task = None
        self._lock = asyncio.Lock()

    async def _drain_stderr(self, stream):
        # Do not surface model-library messages or inherited paths in Web errors.
        # Reading fixed chunks bounds memory even when a native library is noisy.
        while chunk := await stream.read(4096):
            self.stderr_bytes = min(self.stderr_bytes + len(chunk), 2**31 - 1)

    async def _send(self, payload: dict):
        encoded = json.dumps(payload, ensure_ascii=False).encode() + b"\n"
        if len(encoded) > MAX_MESSAGE:
            raise AsrError("语音识别请求过大", retryable=False)
        if self.process is None or self.process.returncode is not None:
            raise AsrError("语音识别进程已退出")
        self.process.stdin.write(encoded)
        await self.process.stdin.drain()

    async def _read(self) -> dict:
        process = self.process
        if process is None:
            raise AsrError("语音识别进程已停止")
        try:
            line = await process.stdout.readline()
            if not line or len(line) > MAX_MESSAGE:
                raise AsrError("语音识别进程未返回有效响应")
            response = json.loads(line)
        except (ValueError, UnicodeDecodeError) as exc:
            raise AsrError("语音识别进程响应格式异常") from exc
        if not isinstance(response, dict):
            raise AsrError("语音识别进程响应格式异常")
        if response.get("type") == "error":
            raise AsrError("语音识别模型或音频暂不可用", retryable=response.get("retryable") is not False)
        return response

    async def start(self):
        async with self._lock:
            if self.ready and self.process and self.process.returncode is None:
                return
            await self.stop()
            environment = {name: os.environ[name] for name in ENVIRONMENT_NAMES if name in os.environ}
            cache_root = self.settings.cache_dir.resolve()
            environment.update(
                HF_HOME=str(cache_root / "huggingface"),
                HF_HUB_CACHE=str(cache_root / "huggingface/hub"),
                HF_XET_CACHE=str(cache_root / "huggingface/xet"),
                XDG_CACHE_HOME=str(cache_root),
                HF_HUB_DISABLE_IMPLICIT_TOKEN="1", HF_HUB_DISABLE_TELEMETRY="1",
                TOKENIZERS_PARALLELISM="false", PYTHONDONTWRITEBYTECODE="1",
                OMP_NUM_THREADS=str(self.settings.whisper_threads),
                OPENBLAS_NUM_THREADS=str(self.settings.whisper_threads),
                MKL_NUM_THREADS=str(self.settings.whisper_threads),
            )
            spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                *self.command, cwd=ROOT, env=environment, limit=MAX_MESSAGE + 1,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ))
            try:
                self.process = await asyncio.shield(spawn)
                self._stderr_task = asyncio.create_task(self._drain_stderr(self.process.stderr))
                configuration = {
                    "model": self.settings.whisper_model, "cache_dir": str(self.settings.cache_dir),
                    "audio_root": str((self.settings.data_dir / "audio").resolve()),
                    "device": self.settings.whisper_device, "compute_type": self.settings.whisper_compute_type,
                    "threads": self.settings.whisper_threads,
                }
                async with asyncio.timeout(self.load_timeout):
                    await self._send(configuration)
                    response = await self._read()
                if response.get("type") != "ready":
                    raise AsrError("语音识别模型初始化响应异常")
                self.ready = True
            except asyncio.CancelledError:
                if self.process is None:
                    self.process = await spawn
                await self.stop()
                raise
            except (AsrError, OSError, TimeoutError) as exc:
                await self.stop()
                if isinstance(exc, AsrError):
                    raise
                raise AsrError("语音识别模型初始化失败") from exc

    async def transcribe(self, path: Path, language_hint: str | None = None) -> dict:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to((self.settings.data_dir / "audio").resolve()) or resolved.suffix != ".wav":
            raise AsrError("录音路径无效", retryable=False)
        if not resolved.is_file():
            raise AsrError("录音文件不存在", retryable=False)
        async with self._lock:
            if not self.ready:
                raise AsrError("语音识别模型尚未就绪")
            identifier = uuid4().hex
            try:
                async with asyncio.timeout(self.job_timeout):
                    await self._send({"type":"transcribe", "request_id":identifier, "path":str(resolved), "language_hint":language_hint})
                    response = await self._read()
                if response.get("type") != "result" or response.get("request_id") != identifier:
                    raise AsrError("语音识别响应不匹配")
                result = response.get("result")
                if not isinstance(result, dict) or not isinstance(result.get("original"), str):
                    raise AsrError("语音识别结果无效")
                if result.get("language") is not None and not isinstance(result["language"], str):
                    raise AsrError("语音识别语言无效")
                probability = result.get("language_probability", 0)
                if not isinstance(probability, (int, float)) or not math.isfinite(probability) or not 0 <= probability <= 1:
                    raise AsrError("语音识别置信度无效")
                # Only these public result fields can update a stored call.
                return {key: result[key] for key in ("original", "language", "language_probability", "speech_detected") if key in result}
            except asyncio.CancelledError:
                await self.stop()
                raise
            except (AsrError, OSError, TimeoutError) as exc:
                await self.stop()
                if isinstance(exc, AsrError):
                    raise
                raise AsrError("语音识别进程中断或处理超时") from exc

    async def stop(self):
        process, self.process = self.process, None
        self.ready = False
        if process and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except TimeoutError as exc:
                    raise AsrError("语音识别进程未能在停止期限内退出") from exc
        if self._stderr_task:
            self._stderr_task.cancel()
            await asyncio.gather(self._stderr_task, return_exceptions=True)
            self._stderr_task = None
