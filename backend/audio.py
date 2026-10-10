import json
import os
import re
import tempfile
import wave
from datetime import date, datetime
from itertools import count
from pathlib import Path
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")


def sync_directory(path: Path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def valid_recording(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != 8000:
                return False
            size = audio.getnframes() * 2
            return 0 < size <= 64 * 1024 * 1024 and len(audio.readframes(audio.getnframes())) == size
    except (OSError, wave.Error, EOFError):
        return False


def recording_name(call: dict) -> str:
    timestamp = datetime.fromtimestamp(call["started_at"], ZoneInfo(call.get("recording_timezone", "Asia/Shanghai")))
    label = str(call.get("callsign") or "unknown")
    if label == str(call["source_id"]):
        label = "unknown"
    label = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-_")[:48] or "unknown"
    return f"{timestamp:%Y%m%d}/{timestamp:%H-%M-%S}_TS{int(call['slot'])}_{int(call['source_id'])}_{label}.wav"


def _candidate_names(name: str):
    yield name
    path = Path(name)
    for number in count(2):
        yield str(path.with_name(f"{path.stem}-{number:02d}.wav"))


def audio_path(directory: Path, call: dict) -> Path:
    filename = call.get("audio_filename") or f"{call['id']}.wav"
    if not isinstance(filename, str) or not re.fullmatch(r"(?:[0-9]{8}/)?[A-Za-z0-9_-]+\.wav", filename):
        raise ValueError("Invalid recording filename")
    if "/" in filename:
        date.fromisoformat(filename.split("/")[0])
    root = directory.resolve()
    path = root / filename
    if not path.resolve().is_relative_to(root):
        raise ValueError("Recording path escapes audio directory")
    return path


def write_recording(directory: Path, call: dict, pcm: bytes) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    initial = audio_path(directory, {**call, "audio_filename": recording_name(call)})
    initial.parent.mkdir(parents=True, exist_ok=True)
    sync_directory(directory.parent)
    sync_directory(directory)
    with tempfile.NamedTemporaryFile(dir=initial.parent, prefix=".recording-", suffix=".part", delete=False) as output:
        temporary = Path(output.name)
        try:
            with wave.open(output, "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8000)
                audio.writeframes(pcm)
            output.flush()
            os.fsync(output.fileno())
            for filename in _candidate_names(recording_name(call)):
                path = audio_path(directory, {**call, "audio_filename": filename})
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    continue
                sync_directory(path.parent)
                return filename
        finally:
            temporary.unlink(missing_ok=True)


def migrate_recordings(store, directory: Path, callsigns: dict | None = None) -> dict:
    """Rename legacy WAVs while keeping old or new paths valid after interruption."""
    rows = store.connection.execute("SELECT document FROM calls ORDER BY started, id").fetchall()
    report = {"renamed": [], "skipped": [], "unchanged": 0}
    for row in rows:
        call = json.loads(row[0])
        try:
            if call.get("_audio_migration_source"):
                target = audio_path(directory, call)
                legacy_name = call["_audio_migration_source"]
                legacy = audio_path(directory, {**call, "audio_filename": legacy_name})
                if target.is_symlink() or not target.is_file():
                    raise ValueError("Interrupted migration target is missing")
                if legacy.is_file():
                    if legacy.is_symlink() or not target.samefile(legacy):
                        raise ValueError("Interrupted migration source does not match target")
                    legacy.unlink()
                clean = {key: value for key, value in call.items() if key != "_audio_migration_source"}
                store.save(clean)
                report["renamed"].append({"id": call["id"], "old": legacy_name, "new": call["audio_filename"]})
                continue
            legacy = audio_path(directory, call)
            if legacy.is_symlink():
                raise ValueError("Legacy recording must be a regular file")
            if not legacy.is_file():
                report["skipped"].append(call["id"])
                continue
            if call.get("audio_filename") and len(Path(call["audio_filename"]).parts) == 2:
                report["unchanged"] += 1
                continue
            naming_call = call
            if callsigns and (not call.get("callsign") or call["callsign"] == str(call["source_id"])):
                entry = callsigns.get(str(call["source_id"]), {})
                naming_call = {**call, "callsign": entry.get("callsign") or call.get("callsign")}
            for filename in _candidate_names(recording_name(naming_call)):
                target = audio_path(directory, {**call, "audio_filename": filename})
                target.parent.mkdir(parents=True, exist_ok=True)
                if target == legacy:
                    report["unchanged"] += 1
                    break
                try:
                    # Both names reference the same bytes until the DB commit.
                    os.link(legacy, target)
                except FileExistsError:
                    if target.is_symlink() or not target.samefile(legacy):
                        continue
                legacy_name = str(legacy.relative_to(directory.resolve()))
                store.save({**call, "audio_filename": filename, "_audio_migration_source": legacy_name})
                legacy.unlink()
                store.save({**call, "audio_filename": filename})
                report["renamed"].append({"id": call["id"], "old": legacy_name, "new": filename})
                break
        except (KeyError, ValueError, TypeError):
            report["skipped"].append(call.get("id", "unknown"))
    return report
