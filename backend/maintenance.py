"""Offline, verified backup/restore and data checks; never loads API credentials."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from backend.audio import audio_path
from backend.config import ROOT
from backend.connection import connection_backup_files
from backend.locking import DataDirectoryLock
from backend.storage_migrations import backup_database
from backend.translation_settings import translation_backup_files


def _documents(database: Path):
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Database integrity check failed")
        return [json.loads(row[0]) for row in connection.execute("SELECT document FROM calls")]


def audit_data(directory: Path) -> dict:
    documents = _documents(directory / "calls.sqlite3")
    referenced = set()
    missing = []
    for call in documents:
        if not call.get("audio_url"):
            continue
        try:
            path = audio_path(directory / "audio", call)
            referenced.add(path)
            if not path.is_file():
                missing.append(call["id"])
        except ValueError:
            missing.append(call["id"])
    files = set((directory / "audio").glob("*/*.wav")) | set((directory / "audio").glob("*.wav"))
    return {"calls":len(documents), "recordings":len(files), "missing_recordings":missing, "unreferenced_recordings":[str(p.relative_to(directory)) for p in sorted(files - referenced)], "spool_files":len(list((directory / ".recording-spool").glob("*.wav"))), "integrity":"ok"}


def backup_data(directory: Path, destination: Path) -> dict:
    if destination.exists():
        raise ValueError("Backup destination already exists")
    destination.mkdir(parents=True, mode=0o700)
    with closing(sqlite3.connect(directory / "calls.sqlite3")) as connection:
        backup_database(connection, destination / "calls.sqlite3")
    documents = _documents(destination / "calls.sqlite3")
    for call in documents:
        if not call.get("audio_url"):
            continue
        source = audio_path(directory / "audio", call)
        if not source.is_file():
            raise RuntimeError("A referenced recording is missing; backup is incomplete")
        relative = source.relative_to((directory / "audio").resolve())
        target = destination / "audio" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for name in ("radioids.json",):
        source = directory / name
        if source.is_file():
            shutil.copy2(source, destination / name)
    if (directory / ".recording-spool").exists():
        shutil.copytree(directory / ".recording-spool", destination / ".recording-spool")
    for source in [*connection_backup_files(directory), *translation_backup_files(directory)]:
        target = destination / source.relative_to(directory)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(source, target)
    entries = []
    for path in sorted(p for p in destination.rglob("*") if p.is_file()):
        entries.append({"path":str(path.relative_to(destination)), "bytes":path.stat().st_size, "sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = {"format":1, "created_at":datetime.now(UTC).isoformat(), "files":entries}
    (destination / "backup-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return {"destination":str(destination), "files":len(entries), "recordings":sum(entry["path"].startswith("audio/") for entry in entries), "verified":True}


def restore_data(source: Path, directory: Path) -> dict:
    manifest = json.loads((source / "backup-manifest.json").read_text())
    if manifest.get("format") != 1:
        raise ValueError("Unsupported backup format")
    if any(path.name != ".starwave.lock" for path in directory.iterdir()):
        raise ValueError("Restore requires an empty DATA_DIR; existing data will not be overwritten")
    names = [entry["path"] for entry in manifest["files"]]
    if len(names) != len(set(names)) or "calls.sqlite3" not in names:
        raise ValueError("Backup manifest is missing its database or contains duplicate files")
    for entry in manifest["files"]:
        relative = Path(entry["path"])
        path = source / relative
        if relative.is_absolute() or not path.resolve().is_relative_to(source.resolve()) or path.is_symlink():
            raise ValueError("Invalid backup path")
        if not path.is_file() or path.stat().st_size != entry["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("Backup verification failed")
    documents = _documents(source / "calls.sqlite3")
    for filename in [*connection_backup_files(source), *translation_backup_files(source)]:
        if str(filename.relative_to(source)) not in names:
            raise ValueError("Backup manifest is missing a connection or translation credential file")
    for call in documents:
        if call.get("audio_url"):
            referenced = audio_path(source / "audio", call)
            relative = "audio/" + str(referenced.relative_to((source / "audio").resolve()))
            if relative not in names:
                raise ValueError("Backup manifest is missing a referenced recording")
    for entry in manifest["files"]:
        relative = Path(entry["path"])
        destination = directory / relative
        if not destination.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Restore path escapes DATA_DIR")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(source / relative, destination)
    return {"files":len(manifest["files"]), "restored":True}


def prune_audio(store, directory: Path, days: int, now: float | None = None) -> dict:
    if days <= 0:
        return {"removed":0, "bytes":0, "updated":[]}
    cutoff = (time.time() if now is None else now) - days * 86400
    with store.lock:
        rows = store.connection.execute("SELECT document FROM calls WHERE started < ? AND status IN ('complete','transcribed','failed','discarded','short')", (cutoff,)).fetchall()
    removed, reclaimed = 0, 0
    updated = []
    for row in rows:
        call = json.loads(row[0])
        if not call.get("audio_url"):
            continue
        with store.lock:
            pending = store.connection.execute("SELECT 1 FROM jobs WHERE call_id=? AND state IN ('ready','running','retry')", (call["id"],)).fetchone()
        if pending:
            continue
        path = audio_path(directory / "audio", call)
        size = path.stat().st_size if path.is_file() else 0
        # The operator opted into this policy. No text or unfinished job is deleted.
        store.patch_call(call["id"], {"audio_url":None, "audio_expired":True})
        path.unlink(missing_ok=True)
        removed += 1
        reclaimed += size
        updated.append(call["id"])
    return {"removed":removed, "bytes":reclaimed, "updated":updated}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("DATA_DIR", str(ROOT / "data"))))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("audit")
    backup = commands.add_parser("backup")
    backup.add_argument("--destination", type=Path, required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--source", type=Path, required=True)
    prune = commands.add_parser("prune-audio")
    prune.add_argument("--days", type=int, required=True)
    retry = commands.add_parser("retry-failed")
    retry.add_argument("--stage", choices=("transcription", "translation"))
    args = parser.parse_args()
    directory = args.data_dir.resolve()
    with DataDirectoryLock(directory):
        if args.command == "audit":
            result = audit_data(directory)
        elif args.command == "backup":
            result = backup_data(directory, args.destination.resolve())
        elif args.command == "restore":
            result = restore_data(args.source.resolve(), directory)
        else:
            from backend.store import Store
            store = Store(directory / "calls.sqlite3")
            try:
                result = {"reset_jobs":store.reset_failed_jobs(args.stage)} if args.command == "retry-failed" else prune_audio(store, directory, args.days)
            finally:
                store.close()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
