"""Versioned SQLite migrations and verified snapshots; never rewrite audio paths."""
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

SCHEMA_VERSION = 2


def backup_database(connection: sqlite3.Connection, destination: Path) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(destination)) as backup:
            connection.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Database backup failed integrity verification")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def initialize_storage(connection: sqlite3.Connection, path: Path, backfill) -> Path | None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"Database schema {version} is newer than supported schema {SCHEMA_VERSION}")
    backup = None
    if version < SCHEMA_VERSION:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables and "calls" not in tables:
            raise RuntimeError("Unrecognized legacy database schema")
        if tables:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            destination = path.parent / "backups" / f"{path.stem}-before-schema-v{version}-{stamp}-{uuid4().hex[:8]}.sqlite3"
            backup = backup_database(connection, destination)
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            if version < 1:
                connection.execute("CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, started REAL NOT NULL, status TEXT NOT NULL, document TEXT NOT NULL)")
                connection.execute("""CREATE TABLE jobs (
                    id INTEGER PRIMARY KEY,
                    call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
                    stage TEXT NOT NULL CHECK(stage IN ('transcription','translation')),
                    state TEXT NOT NULL CHECK(state IN ('ready','running','retry','done','failed')),
                    priority INTEGER NOT NULL CHECK(priority IN (0,1)),
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                    available_at REAL NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_token TEXT,
                    lease_until REAL,
                    last_error TEXT,
                    UNIQUE(call_id, stage)
                )""")
                connection.execute("CREATE INDEX jobs_available ON jobs(stage,state,available_at)")
                connection.execute("CREATE INDEX calls_started ON calls(started DESC)")
                backfill()
            connection.execute("CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("PRAGMA user_version=2")
    if connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
        raise RuntimeError("Database could not enable WAL journaling")
    connection.execute("PRAGMA synchronous=FULL")
    return backup
