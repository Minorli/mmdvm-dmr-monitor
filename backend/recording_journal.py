"""Atomic one-second snapshots recover active audio after a hard process exit."""

import os
import re
import tempfile
import time
import wave
from pathlib import Path

from backend.audio import sync_directory


class RecordingJournal:
    def __init__(self, data_dir: Path, interval: float = 1):
        self.data_dir = data_dir.resolve()
        self.root = data_dir / ".recording-spool"
        self.interval = interval
        self.updated: dict[str, float] = {}

    def path(self, identifier: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ValueError("Invalid recording identifier")
        path = self.root / (identifier + ".wav")
        if not path.resolve().is_relative_to(self.data_dir):
            raise ValueError("Recording journal escapes DATA_DIR")
        return path

    def snapshot(self, segment, force: bool = False):
        now = time.monotonic()
        if not force and now - self.updated.get(segment.id, 0) < self.interval:
            return
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = self.path(segment.id)
        with tempfile.NamedTemporaryFile(dir=self.root, prefix=".snapshot-", suffix=".part", delete=False) as output:
            temporary = Path(output.name)
            try:
                with wave.open(output, "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(8000)
                    audio.writeframes(segment.pcm)
                output.flush()
                os.fsync(output.fileno())
                os.replace(temporary, destination)
                sync_directory(self.root)
                sync_directory(self.data_dir)
                self.updated[segment.id] = now
            finally:
                temporary.unlink(missing_ok=True)

    def recover(self, identifier: str) -> bytes | None:
        path = self.path(identifier)
        if not path.is_file():
            return None
        with wave.open(str(path), "rb") as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != 8000:
                raise ValueError("Invalid recording snapshot format")
            if audio.getnframes() * 2 > 64 * 1024 * 1024:
                raise ValueError("Recording snapshot is too large")
            pcm = audio.readframes(audio.getnframes())
            if len(pcm) != audio.getnframes() * 2:
                raise ValueError("Incomplete recording snapshot")
            return pcm

    def discard(self, identifier: str):
        self.path(identifier).unlink(missing_ok=True)
        self.updated.pop(identifier, None)
