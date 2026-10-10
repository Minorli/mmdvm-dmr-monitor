"""Supervised, independently scheduled ASR and translation backed by SQLite jobs."""
import asyncio
import logging
import random
import time
from uuid import uuid4

import httpx

from backend.ai import TranslationClient
from backend.asr_client import AsrClient, AsrError
from backend.audio import audio_path

log = logging.getLogger(__name__)


class ProcessingPipeline:
    def __init__(self, settings, store, on_call, supervisor):
        self.settings, self.store = settings, store
        self.on_call, self.supervisor = on_call, supervisor
        self.translator = TranslationClient(settings.api_url, settings.api_key, settings.translation_model, api_mode=settings.translation_api, timeout=settings.request_timeout)
        self.translation_enabled = settings.translation_enabled and bool(settings.api_key)
        self.owner = ""
        self.running = False
        self._stopping = False
        self.clients = []
        self.model_states = {}
        self.stream_languages = {}
        self.processing_transcription = 0
        self.processing_translation = 0
        self.last_transcription_seconds = None
        self.last_translation_seconds = None

    async def reconfigure(self, settings) -> None:
        if self.running or self._stopping:
            raise RuntimeError("请先停止监听，再修改文字处理设置")
        await self.translator.close()
        self.settings = settings
        self.translator = TranslationClient(settings.api_url, settings.api_key, settings.translation_model, api_mode=settings.translation_api, timeout=settings.request_timeout)
        self.translation_enabled = settings.translation_enabled and bool(settings.api_key)

    def start(self) -> None:
        if self._stopping:
            raise RuntimeError("Processing is stopping")
        if self.running:
            return
        self.running = True
        self.owner = uuid4().hex
        self.clients = [AsrClient(self.settings) for _ in range(self.settings.transcription_workers)]
        self.model_states = {index:"loading" for index in range(len(self.clients))}
        for index, client in enumerate(self.clients):
            self.supervisor.spawn(f"asr-{index}", self._transcribe_worker(index, client, self.owner), group="processing")
        if self.translation_enabled:
            for index in range(self.settings.translation_workers):
                self.supervisor.spawn(f"translate-{index}", self._translate_worker(self.owner), group="processing")

    async def stop(self) -> None:
        self.running = False
        self._stopping = True
        try:
            affected = [call["id"] for call in self.store.pending_calls() if call["status"] in {"transcribing", "translating"}]
            try:
                await self.supervisor.cancel_group("processing")
            finally:
                try:
                    await asyncio.gather(*(client.stop() for client in self.clients))
                    await self.translator.close()
                finally:
                    if self.owner:
                        self.store.release_jobs(self.owner)
                    for call_id in affected:
                        self._publish(call_id)
                    self.model_states = {}
        finally:
            self._stopping = False

    def _publish(self, call_id):
        call = self.store.get(call_id)
        if call:
            self.on_call(call)

    def metrics(self) -> dict:
        counts = self.store.job_counts()
        states = tuple(self.model_states.values())
        state = "paused" if not self.running else "local" if "local" in states else "loading" if "loading" in states else "unavailable"
        return {
            "transcription":state, "translation":"ready" if self.translation_enabled else "disabled",
            "pending_transcription":counts["transcription"]["ready"] + counts["transcription"]["retry"],
            "pending_translation":counts["translation"]["ready"] + counts["translation"]["retry"],
            "processing_transcription":self.processing_transcription,
            "processing_translation":self.processing_translation,
            "last_transcription_seconds":self.last_transcription_seconds,
            "last_translation_seconds":self.last_translation_seconds,
            "model_workers_ready":states.count("local"),
            "transcription_workers":self.settings.transcription_workers,
            "translation_workers":self.settings.translation_workers if self.translation_enabled else 0,
            "live_jobs":sum(bucket["live"] for bucket in counts.values()),
            "history_jobs":sum(bucket["history"] for bucket in counts.values()), "job_counts":counts,
        }

    def _retry(self, job, exc):
        call = self.store.get(job["call_id"])
        if not call:
            return
        retryable = getattr(exc, "retryable", isinstance(exc, (httpx.HTTPError, TimeoutError)))
        delay = min(self.settings.retry_base_seconds * 2**min(job["attempts"], 10), 300)
        retry_after = getattr(exc, "retry_after", None)
        if isinstance(retry_after, (int, float)) and 0 <= retry_after <= 3600:
            delay = max(delay, retry_after)
        delay += random.uniform(0, min(delay * .1, 1))
        reason = "语音识别暂时失败，录音已保留" if job["stage"] == "transcription" else "翻译暂时失败，原文已保留"
        if self.store.retry_job(job["id"], job["lease_token"], call, reason, retryable=retryable, max_attempts=self.settings.retry_attempts, delay_seconds=delay):
            self._publish(call["id"])
        log.warning("%s job failed (%s)", job["stage"], type(exc).__name__)

    async def _transcribe_worker(self, index, client, owner):
        load_failures = 0
        try:
            while self.running:
                if not client.ready:
                    self.model_states[index] = "loading"
                    try:
                        await client.start()
                    except (AsrError, OSError) as exc:
                        self.model_states[index] = "unavailable"
                        load_failures += 1
                        log.warning("Speech model unavailable (%s)", type(exc).__name__)
                        await asyncio.sleep(min(self.settings.retry_base_seconds * 2**min(load_failures-1, 10), 60))
                        continue
                    self.model_states[index] = "local"
                    load_failures = 0
                job = self.store.claim_job("transcription", owner, self.settings.job_lease_seconds)
                if job is None:
                    await asyncio.sleep(.2)
                    continue
                self.processing_transcription += 1
                try:
                    self._publish(job["call_id"])
                    call = self.store.get(job["call_id"])
                    if call["duration"] < self.settings.min_call_seconds:
                        if self.store.finish_job(job["id"], job["lease_token"], {**call, "status":"discarded", "error":None}):
                            self._publish(call["id"])
                        continue
                    stream = (call["slot"], call["source_id"], call.get("direction"), call.get("stream_id"))
                    hint = self.stream_languages.get(stream) if call.get("stream_id") else None
                    began = time.monotonic()
                    try:
                        result = await client.transcribe(audio_path(self.settings.data_dir / "audio", call), hint)
                    except (AsrError, OSError, ValueError) as exc:
                        self._retry(job, exc)
                        continue
                    self.last_transcription_seconds = round(time.monotonic()-began, 2)
                    updated = {**self.store.get(call["id"]), **result, "error":None}
                    if not result["original"]:
                        updated = {**updated, "status":"discarded" if result.get("speech_detected") is False else "failed"}
                        if updated["status"] == "failed":
                            updated = {**updated, "error":"未能识别文字，可以回听录音"}
                        next_stage = None
                    else:
                        updated = {**updated, "status":"translation_pending" if self.translation_enabled else "transcribed"}
                        next_stage = "translation" if self.translation_enabled else None
                        if call.get("stream_id") and result.get("language_probability", 0) >= .8:
                            self.stream_languages[stream] = result.get("language")
                            if len(self.stream_languages) > 256:
                                self.stream_languages.pop(next(iter(self.stream_languages)))
                    if self.store.finish_job(job["id"], job["lease_token"], updated, next_stage=next_stage):
                        self._publish(call["id"])
                finally:
                    self.processing_transcription -= 1
        finally:
            await client.stop()

    async def _translate_worker(self, owner):
        while self.running:
            job = self.store.claim_job("translation", owner, self.settings.job_lease_seconds)
            if job is None:
                await asyncio.sleep(.2)
                continue
            self.processing_translation += 1
            try:
                self._publish(job["call_id"])
                call = self.store.get(job["call_id"])
                if not call.get("original"):
                    self._retry(job, ValueError("Missing transcript"))
                    continue
                began = time.monotonic()
                try:
                    async with asyncio.timeout(min(self.settings.request_timeout, self.settings.job_lease_seconds * .8)):
                        result = await self.translator.translate(call["original"])
                except Exception as exc:
                    if not isinstance(exc, (ValueError, httpx.HTTPError, TimeoutError)) and not hasattr(exc, "retryable"):
                        raise
                    self._retry(job, exc)
                    continue
                self.last_translation_seconds = round(time.monotonic()-began, 2)
                updated = {**self.store.get(call["id"]), **result, "status":"complete", "error":None}
                if self.store.finish_job(job["id"], job["lease_token"], updated):
                    self._publish(call["id"])
            finally:
                self.processing_translation -= 1
