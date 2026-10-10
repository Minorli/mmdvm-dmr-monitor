"""Application lifecycle, operator controls, and DMR recording orchestration."""

import asyncio
import logging
import os
import shutil
import sqlite3
import time
import wave

import httpx

from backend import __version__
from backend.audio import audio_path, valid_recording, write_recording
from backend.capture import CaptureError, SSHCapture
from backend.codec import DMRDecoder
from backend.config import Settings
from backend.connection import ConnectionConflict, ConnectionManager
from backend.connection_probe import prepare_connection, test_connection
from backend.directory import Directory
from backend.maintenance import prune_audio
from backend.packets import parse_packet
from backend.processing import ProcessingPipeline
from backend.recording_journal import RecordingJournal
from backend.sessions import Segment, SessionTracker
from backend.store import Store
from backend.supervision import TaskSupervisor
from backend.translation_settings import TranslationSettingsManager

log = logging.getLogger(__name__)


class Runtime:
    def __init__(self, settings: Settings):
        self.connections = ConnectionManager(settings)
        self.translations = TranslationSettingsManager(settings)
        self.settings = settings = self.effective_settings()
        self.store = Store(settings.data_dir / "calls.sqlite3", settings.min_call_seconds, live_job_age_seconds=settings.live_job_age_seconds)
        self.directory = Directory(settings.data_dir / "radioids.json")
        self.tracker = SessionTracker(max_seconds=settings.max_segment_seconds)
        self.journal = RecordingJournal(settings.data_dir)
        self.decoders: dict[tuple, DMRDecoder] = {}
        self.decoder_streams = {}
        self.listeners: set[asyncio.Queue] = set()
        self.supervisor = TaskSupervisor()
        self.pipeline = ProcessingPipeline(settings, self.store, self.publish_call, self.supervisor)
        self.lookup_tasks: dict[int, asyncio.Task] = {}
        self.capture_source = SSHCapture(settings)
        self.capture_state = "offline"
        self.listening_state = "stopped"
        self.last_packet_at = None
        self.last_capture_heartbeat = None
        self.message = "未开始监听"
        self.control_lock = asyncio.Lock()
        self.started = False
        self.last_tg: dict[int, int] = {slot: self.store.latest_tg(slot) for slot in (1, 2)}
        self._status_version = 0
        self.filtered_short = 0
        self.invalid_packets = 0

    async def start(self):
        if self.started:
            return
        self.started = True
        self.store.recover_all_jobs()
        for call in self.store.pending_calls():
            if call["status"] != "recording":
                continue
            try:
                completed_audio = bool(call.get("audio_filename")) and valid_recording(audio_path(self.settings.data_dir / "audio", call))
            except ValueError:
                completed_audio = False
            if not completed_audio:
                try:
                    pcm = self.journal.recover(call["id"])
                except (OSError, ValueError, wave.Error, EOFError):
                    pcm = None
                if pcm and len(pcm) / 16000 >= self.settings.min_call_seconds:
                    filename = write_recording(self.settings.data_dir / "audio", call, pcm)
                    restored = {**call, "duration":len(pcm) / 16000, "ended_at":call["started_at"] + len(pcm) / 16000, "audio_filename":filename, "audio_url":f"/api/audio/{call['id']}", "status":"queued", "recovered_recording":True}
                    self.store.save(restored)
                    self.journal.discard(call["id"])
                    continue
                self.store.fail_call({**call, "ended_at":call.get("ended_at") or call["started_at"] + call["duration"]}, "进程中断，录音未完成")
        self.store.recover_legacy_calls()
        self.supervisor.spawn("status-publisher", self.publish_status())
        if self.settings.audio_retention_days:
            self.supervisor.spawn("audio-retention", self.retention())
        if self.store.get_setting("listening_requested", False) is True:
            try:
                await self.start_listening(persist=False)
            except ValueError:
                self.listening_state = "error"
                self.message = "监听配置无效，请检查后台配置"

    async def stop(self):
        await self.stop_listening(persist=False)
        await self.supervisor.close()
        self.store.close()
        self.started = False

    def validate_listener(self):
        if not self.settings.pi_host:
            raise ValueError("请先配置 PI_STAR_HOST")
        if self.settings.require_explicit_ssh and (not (self.settings.ssh_key_file or self.settings.ssh_password_file) or not self.settings.ssh_known_hosts_file):
            raise ValueError("请配置 SSH 密钥或密码，以及可信的主机密钥")
        for path in (self.settings.ssh_key_file, self.settings.ssh_password_file, self.settings.ssh_known_hosts_file):
            if path and (not path.is_file() or not path.stat().st_size or not os.access(path, os.R_OK)):
                raise ValueError("SSH凭据文件未配置或不可读取")
        try:
            if shutil.disk_usage(self.settings.data_dir).free < 64 * 1024 * 1024:
                raise ValueError("数据目录剩余空间不足，请先释放空间")
            decoder = DMRDecoder()
            decoder.close()
        except OSError as exc:
            raise ValueError("数据目录无法访问") from exc

    async def start_listening(self, persist: bool = True) -> dict:
        async with self.control_lock:
            if self.listening_state in {"starting", "running"}:
                return self.status()
            self.validate_listener()
            if persist:
                self.store.set_setting("listening_requested", True)
            self.listening_state = "starting"
            self.capture_state = "connecting"
            self.message = "正在连接电台"
            self.last_capture_heartbeat = None
            self.pipeline.start()
            self.supervisor.spawn("capture", self.capture(), group="capture")
            self.supervisor.spawn("recording-watchdog", self.watchdog(), group="capture")
            self.listening_state = "running"
            self.publish("status", self.status())
            log.info("Listening started (source=%s)", "operator" if persist else "restart")
            return self.status()

    async def configure_connection(self, payload: dict, test: bool = False) -> dict:
        async with self.control_lock:
            if self.listening_state != "stopped":
                raise ConnectionConflict("请先停止监听，再修改或测试连接设置")
            details, key, pending = await prepare_connection(self.connections, payload)
            if pending:
                return pending
            if test:
                return await test_connection(self.connections, details, key)
            public = self.connections.save(details, key.line(details))
            self.settings = self.effective_settings()
            await self.pipeline.reconfigure(self.settings)
            self.capture_source = SSHCapture(self.settings)
            self.last_packet_at = None
            log.info("Connection settings saved (host=%s port=%s user=%s mode=%s)", details.host, details.port, details.username, details.auth_mode)
            self.publish("status", self.status())
            return {"ok":True, **public}

    def effective_settings(self) -> Settings:
        return self.translations.settings(base=self.connections.settings())

    async def configure_translation(self, payload: dict) -> dict:
        async with self.control_lock:
            if self.listening_state != "stopped":
                raise ConnectionConflict("请先停止监听，再修改翻译设置")
            details = self.translations.draft(payload)
            public = self.translations.save(details)
            self.settings = self.effective_settings()
            await self.pipeline.reconfigure(self.settings)
            self.capture_source = SSHCapture(self.settings)
            log.info("Translation settings saved (enabled=%s api_mode=%s)", details.enabled, details.api_mode)
            self.publish("status", self.status())
            return {"ok":True, **public}

    async def stop_listening(self, persist: bool = True) -> dict:
        async with self.control_lock:
            if persist:
                self.store.set_setting("listening_requested", False)
                log.info("Operator requested listening stop")
            if self.listening_state == "stopped":
                return self.status()
            self.listening_state = "stopping"
            self.publish("status", self.status())
            await asyncio.gather(self.supervisor.cancel_group("capture"), self.pipeline.stop())
            await self.capture_source.close()
            for segment in self.tracker.finish_all(time.time()):
                self.finish(segment)
            for task in self.lookup_tasks.values():
                task.cancel()
            await asyncio.gather(*self.lookup_tasks.values(), return_exceptions=True)
            self.lookup_tasks.clear()
            for decoder in self.decoders.values():
                decoder.close()
            self.decoders.clear()
            self.decoder_streams.clear()
            self.tracker = SessionTracker(max_seconds=self.settings.max_segment_seconds)
            self.capture_state = "offline"
            self.listening_state = "stopped"
            self.message = "未开始监听"
            self.publish("status", self.status())
            log.info("Listening stopped; capture and processing tasks have exited")
            return self.status()

    def publish(self, event: str, data: dict):
        for queue in tuple(self.listeners):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(("resync", {}))
            else:
                queue.put_nowait((event, data))

    def publish_call(self, call: dict):
        self.publish("call", call)

    def save_call(self, call: dict):
        self.store.save(call)
        self.publish_call(call)

    def status(self) -> dict:
        # Version the snapshot when it is made, so delayed SSE/GET responses cannot
        # overwrite a newer control response. UTC microseconds fit JS safe integers.
        self._status_version = max(self._status_version + 1, time.time_ns() // 1000)
        slots = []
        for slot in (1, 2):
            current = [s for key, s in self.tracker.active.items() if key[0] == slot and s.duration >= self.settings.min_call_seconds]
            segment = max(current, key=lambda s: s.last_at) if current else None
            metadata = {"source_id": segment.source_id, "started_at": segment.started_at, "direction": segment.direction, **self.directory.get(segment.source_id)} if segment else {}
            slots.append({"slot": slot, "tg": segment.tg if segment else self.last_tg.get(slot, 0), "active": segment is not None, **metadata})
        metrics = self.pipeline.metrics()
        metrics["model"] = self.settings.translation_model
        metrics["queues"] = {bucket: {stage: metrics["job_counts"][stage][bucket] for stage in ("transcription", "translation")} for bucket in ("live", "history")}
        return {
            "status_version": self._status_version,
            "capture_state": self.capture_state,
            "listening_state": self.listening_state,
            "control_requires_token": bool(self.settings.control_token),
            "last_packet_at": self.last_packet_at,
            "slots": slots,
            "counts": self.store.counts(),
            "ai": metrics,
            "device": {"name": self.settings.device_name, "address": self.settings.pi_host.rsplit("@", 1)[-1], "capture_source": "SSH / DMRD"},
            "message": self.message,
        }

    def health(self) -> dict:
        database_ok = True
        try:
            with self.store.lock:
                self.store.connection.execute("SELECT 1").fetchone()
        except sqlite3.Error:
            database_ok = False
        failed = bool(self.supervisor.failed) or not database_ok
        degraded = self.listening_state in {"starting", "error"} or (self.listening_state == "running" and (self.capture_state != "online" or self.pipeline.metrics()["transcription"] in {"unavailable", "loading"}))
        return {"ok": not failed, "version":__version__, "status": "failed" if failed else "degraded" if degraded else "ok", "listening_state": self.listening_state, "database": "ok" if database_ok else "failed", "tasks": {name: {"group": group, "running": not task.done()} for name, (group, task) in self.supervisor.tasks.items()}, "failed_tasks": dict(self.supervisor.failed)}

    def call_from_segment(self, segment: Segment) -> dict:
        return {"id": segment.id, "slot": segment.slot, "tg": segment.tg, "source_id": segment.source_id, "stream_id": segment.stream_id, "direction": segment.direction, **self.directory.get(segment.source_id), "started_at": segment.started_at, "ended_at": segment.ended_at, "duration": segment.duration, "status": "recording", "original": None, "translated": None, "topic": None, "language": None, "error": None, "audio_url": None, "recording_timezone": self.settings.timezone}

    async def enrich(self, source_id: int):
        try:
            entry = await self.directory.lookup(source_id)
            for call in self.store.list_calls():
                if call["source_id"] == source_id:
                    updated = self.store.patch_call(call["id"], entry)
                    if updated:
                        self.publish_call(updated)
        except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
            log.info("Radio directory unavailable for %s (%s)", source_id, type(exc).__name__)
        finally:
            self.lookup_tasks.pop(source_id, None)

    def handle_packet(self, raw: bytes, at: float, direction: str):
        packet = parse_packet(raw)
        if packet is None or packet.kind == "data":
            return
        self.last_packet_at = at
        self.last_tg[packet.slot] = packet.tg
        key = (packet.slot, direction)
        stream = (packet.stream_id, packet.source_id, packet.tg)
        if self.tracker.is_duplicate(packet, direction):
            return
        pcm = b""
        if packet.kind == "voice":
            decoder = self.decoders.get(key)
            if decoder is None:
                decoder = DMRDecoder()
                self.decoders[key] = decoder
            if self.decoder_streams.get(key) != stream:
                decoder.reset()
                self.decoder_streams[key] = stream
            pcm = decoder.decode(packet.burst)
        for segment in self.tracker.feed(packet, pcm, at, direction):
            self.finish(segment)
        for active in self.tracker.active.values():
            if active.duration < self.settings.min_call_seconds:
                continue
            self.journal.snapshot(active)
            if self.store.get(active.id) is None:
                self.save_call(self.call_from_segment(active))
                if str(active.source_id) not in self.directory.cache and active.source_id not in self.lookup_tasks:
                    self.lookup_tasks[active.source_id] = asyncio.create_task(self.enrich(active.source_id))

    def finish(self, segment: Segment, enqueue: bool = True):
        if segment.duration < self.settings.min_call_seconds:
            self.filtered_short += 1
            existing = self.store.get(segment.id)
            if existing:
                self.save_call({**existing, "status": "discarded", "error": None})
            return
        call = self.store.get(segment.id) or self.call_from_segment(segment)
        self.journal.snapshot(segment, force=True)
        filename = write_recording(self.settings.data_dir / "audio", call, segment.pcm)
        updated = {**call, "ended_at": segment.ended_at, "duration": segment.duration, "audio_url": f"/api/audio/{segment.id}", "audio_filename": filename, "status": "queued"}
        self.store.enqueue_call(updated, "transcription", priority=0)
        self.journal.discard(segment.id)
        self.publish_call(updated)

    async def capture(self):
        while True:
            self.capture_state = "connecting"
            self.message = "正在连接电台"
            try:
                async for event in self.capture_source.events():
                    self.last_capture_heartbeat = time.monotonic()
                    if event["type"] == "ready":
                        self.capture_state = "online"
                        self.message = "监听中"
                    elif event["type"] == "packet":
                        try:
                            payload = bytes.fromhex(event["payload"])
                            direction = event.get("direction", "incoming")
                            if direction not in {"incoming", "outgoing"}:
                                raise ValueError("invalid direction")
                        except (ValueError, KeyError, TypeError):
                            self.invalid_packets += 1
                            continue
                        # Receiver time determines filenames; remote clock drift cannot misfile audio.
                        self.handle_packet(payload, time.time(), direction)
            except asyncio.CancelledError:
                raise
            except CaptureError as exc:
                self.capture_state = "offline"
                self.message = "电台连接中断，正在重连"
                log.warning("Capture reconnect: %s", type(exc).__name__)
            finally:
                for segment in self.tracker.finish_all(time.time()):
                    self.finish(segment)
            self.publish("status", self.status())
            await asyncio.sleep(self.settings.reconnect_seconds)

    async def watchdog(self):
        while True:
            await asyncio.sleep(1)
            for segment in self.tracker.flush_expired(time.time()):
                self.finish(segment)

    async def publish_status(self):
        while True:
            self.publish("status", self.status())
            await asyncio.sleep(1)

    async def retention(self):
        while True:
            try:
                result = await asyncio.to_thread(prune_audio, self.store, self.settings.data_dir, self.settings.audio_retention_days)
                for identifier in result["updated"]:
                    call = self.store.get(identifier)
                    if call:
                        self.publish_call(call)
            except (OSError, sqlite3.Error, ValueError) as exc:
                log.warning("Audio retention could not finish (%s)", type(exc).__name__)
            await asyncio.sleep(3600)
