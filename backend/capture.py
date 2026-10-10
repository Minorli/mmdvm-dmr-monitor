"""Bounded SSH stream transport; the modem's network path remains independent."""

import asyncio
import contextlib
import json
import os
import signal
from pathlib import Path

from backend.config import Settings


class CaptureError(RuntimeError):
    pass


class SSHCapture:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.process: asyncio.subprocess.Process | None = None

    def command(self, password_fd: int | None = None) -> list[str]:
        password_mode = bool(self.settings.ssh_password_file)
        if password_mode and password_fd is None:
            raise CaptureError("SSH口令管道未初始化")
        command = ["ssh", "-T", "-p", str(self.settings.ssh_port), "-o", f"BatchMode={'no' if password_mode else 'yes'}", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2", "-o", "StrictHostKeyChecking=yes", "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes"]
        if password_mode:
            command += ["-o", "NumberOfPasswordPrompts=1", "-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password,keyboard-interactive"]
        elif self.settings.ssh_key_file:
            command += ["-i", str(self.settings.ssh_key_file), "-o", "IdentitiesOnly=yes"]
        if self.settings.ssh_known_hosts_file:
            # OpenSSH parses -o values as ssh_config, including whitespace.
            known_hosts = str(self.settings.ssh_known_hosts_file).replace("\\", "\\\\").replace('"', '\\"')
            command += ["-o", f'UserKnownHostsFile="{known_hosts}"']
        prefix = ["sshpass", "-d", str(password_fd)] if password_mode else []
        return [*prefix, *command, self.settings.pi_host, "sudo -n python3 -u -"]

    @staticmethod
    def _environment() -> dict[str, str]:
        # Preserve SSH identity/locale behavior without forwarding application
        # API keys, control tokens, or an unrelated SSHPASS environment variable.
        names = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TZ", "TERM", "SSH_AUTH_SOCK", "SSH_AGENT_PID")
        return {name: os.environ[name] for name in names if name in os.environ}

    def _password_pipe(self) -> int:
        try:
            with self.settings.ssh_password_file.open("rb") as source:
                password = source.read(4097)
        except OSError:
            raise CaptureError("无法读取SSH口令配置") from None
        password = password.removesuffix(b"\r\n").removesuffix(b"\n")
        if not password or len(password) > 4095 or any(value in password for value in (b"\0", b"\r", b"\n")):
            raise CaptureError("SSH口令配置必须是有效的单行内容")
        read_fd, write_fd = os.pipe()
        try:
            os.set_blocking(write_fd, False)
            payload = password + b"\n"
            if os.write(write_fd, payload) != len(payload):
                raise CaptureError("SSH口令管道无法初始化")
        except BaseException:
            os.close(read_fd)
            raise
        finally:
            os.close(write_fd)
        return read_fd

    @staticmethod
    async def _settle(task: asyncio.Task):
        """Finish resource ownership/cleanup even if Stop cancels repeatedly."""
        cancelled = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                cancelled = exc
        return cancelled

    async def _spawn(self, password_fd: int | None):
        creation = asyncio.create_task(asyncio.create_subprocess_exec(
            *self.command(password_fd), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env=self._environment(), pass_fds=() if password_fd is None else (password_fd,),
            start_new_session=True, limit=4096,
        ))
        try:
            return await asyncio.shield(creation)
        except asyncio.CancelledError:
            # Creation may already have forked a child before returning its
            # Process object. Retrieve ownership and reap it before closing FD.
            with contextlib.suppress(OSError):
                await self._settle(creation)
                process = creation.result()
                cleanup = asyncio.create_task(self._stop_process(process))
                await self._settle(cleanup)
                cleanup.result()
            raise

    async def events(self, script: bytes | None = None):
        password_fd = None
        try:
            if self.settings.ssh_password_file:
                password_fd = self._password_pipe()
            process = await self._spawn(password_fd)
        except OSError as exc:
            raise CaptureError("SSH采集无法启动") from exc
        finally:
            if password_fd is not None:
                os.close(password_fd)
        self.process = process
        try:
            contents = script if script is not None else Path(__file__).with_name("collector.py").read_bytes()
            try:
                process.stdin.write(contents)
                await process.stdin.drain()
                process.stdin.close()
            except OSError as exc:
                raise CaptureError("SSH采集脚本无法送达") from exc
            while True:
                try:
                    line = await asyncio.wait_for(process.stdout.readline(), self.settings.capture_timeout)
                except (TimeoutError, ValueError) as exc:
                    raise CaptureError("采集数据流超时或格式无效") from exc
                if not line:
                    raise CaptureError("SSH采集连接已断开")
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(event, dict) and event.get("type") in {"ready", "heartbeat", "packet"}:
                    yield event
        finally:
            await self.close()

    @staticmethod
    async def _stop_process(process):
        if process:
            # The session leader can have exited while its descendants still
            # own collector pipes. Stop our whole group and drain those pipes.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.communicate(), 3)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.communicate()

    async def close(self):
        process, self.process = self.process, None
        if process:
            cleanup = asyncio.create_task(self._stop_process(process))
            cancelled = await self._settle(cleanup)
            cleanup.result()
            if cancelled:
                raise cancelled
