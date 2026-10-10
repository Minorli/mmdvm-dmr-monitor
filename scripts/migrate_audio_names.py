"""Run with the web service stopped: python -m scripts.migrate_audio_names."""
import json
import os
import sqlite3
from datetime import UTC, datetime

from backend.audio import migrate_recordings
from backend.config import load_settings
from backend.directory import Directory
from backend.store import Store


def main():
    settings = load_settings()
    backup_dir = settings.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
    backup_path = backup_dir / f"calls-before-audio-rename-{stamp}.sqlite3"
    store = Store(settings.data_dir / "calls.sqlite3")
    try:
        with sqlite3.connect(backup_path) as backup:
            store.connection.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Audio migration database backup did not verify")
        directory = Directory(settings.data_dir / "radioids.json")
        report = migrate_recordings(store, settings.data_dir / "audio", directory.cache)
        manifest = backup_dir / f"audio-renames-{stamp}.json"
        manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"renamed": len(report["renamed"]), "skipped": len(report["skipped"]), "unchanged": report["unchanged"], "database_backup": str(backup_path), "manifest": str(manifest)}, ensure_ascii=False))
    finally:
        store.close()


if __name__ == "__main__":
    main()
