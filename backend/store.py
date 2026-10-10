import json
import math
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from uuid import uuid4

from backend.storage_migrations import (
    SCHEMA_VERSION,
    backup_database,
    initialize_storage,
)

STAGES = ("transcription", "translation")
WAITING_STATUS = {"transcription": "queued", "translation": "translation_pending"}
RUNNING_STATUS = {"transcription": "transcribing", "translation": "translating"}
HISTORY_SECONDS = 300


class Store:
    def __init__(self, path: Path, min_duration: float = 0, *, live_job_age_seconds: float = HISTORY_SECONDS):
        if not math.isfinite(live_job_age_seconds) or live_job_age_seconds <= 0:
            raise ValueError("Live job age must be positive and finite")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = RLock()
        self.min_duration = min_duration
        self.live_job_age_seconds = live_job_age_seconds
        self.path = Path(path)
        self._closed = False
        self.schema_version = SCHEMA_VERSION
        try:
            self.connection.execute("PRAGMA busy_timeout=5000")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.migration_backup = initialize_storage(self.connection, self.path, self._recover_legacy_calls)
        except Exception:
            self.connection.close()
            raise

    @contextmanager
    def _transaction(self):
        with self.lock, self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            yield

    def _save(self, call: dict):
        self.connection.execute("INSERT INTO calls VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET started=excluded.started, status=excluded.status, document=excluded.document", (call["id"], call["started_at"], call["status"], json.dumps(call, ensure_ascii=False)))

    def save(self, call: dict):
        with self.lock, self.connection:
            self._save(call)

    def fail_call(self, call: dict, error: str) -> None:
        """Mark an interrupted recording and revoke every outstanding worker token."""
        if not isinstance(error, str):
            raise TypeError("Call error must be text")
        with self._transaction():
            self._save({**call, "status": "failed", "error": error[:200], "audio_url": None})
            self.connection.execute("UPDATE jobs SET state='failed',lease_owner=NULL,lease_token=NULL,lease_until=NULL,last_error=? WHERE call_id=? AND state IN ('ready','running','retry')", (error[:200], call["id"]))

    def patch_call(self, call_id: str, fields: dict) -> dict | None:
        """Merge specified fields into the latest row without reverting unrelated updates."""
        if "id" in fields or "started_at" in fields:
            raise ValueError("Call identity and start time cannot be patched")
        with self._transaction():
            call = self.get(call_id)
            if call is None:
                return None
            updated = {**call, **fields}
            self._save(updated)
            return updated

    def get(self, call_id: str) -> dict | None:
        with self.lock:
            row = self.connection.execute("SELECT document FROM calls WHERE id = ?", (call_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_calls(self, limit: int = 200) -> list[dict]:
        with self.lock:
            rows = self.connection.execute("SELECT document FROM calls WHERE status NOT IN ('short','discarded') AND COALESCE(json_extract(document,'$.duration'),0) >= ? ORDER BY started DESC LIMIT ?", (self.min_duration, limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def latest_tg(self, slot: int) -> int:
        """Slot metadata is independent of the bounded visible call snapshot."""
        if slot not in (1, 2):
            raise ValueError("DMR slot must be 1 or 2")
        with self.lock:
            row = self.connection.execute("SELECT json_extract(document,'$.tg') FROM calls WHERE json_extract(document,'$.slot')=? AND json_type(document,'$.tg')='integer' AND json_extract(document,'$.tg') BETWEEN 1 AND 16777215 ORDER BY started DESC LIMIT 1", (slot,)).fetchone()
        return row[0] if row else 0

    def counts(self) -> dict:
        with self.lock:
            count, translated = self.connection.execute("SELECT COUNT(*), COALESCE(SUM(status='complete'),0) FROM calls WHERE status NOT IN ('short','discarded') AND COALESCE(json_extract(document,'$.duration'),0) >= ?", (self.min_duration,)).fetchone()
        return {"calls": count, "translated": translated}

    def pending_calls(self) -> list[dict]:
        with self.lock:
            rows = self.connection.execute("SELECT document FROM calls WHERE status IN ('recording','queued','transcribing','translation_pending','translating') ORDER BY started ASC").fetchall()
        return [json.loads(row[0]) for row in rows]

    @staticmethod
    def _stage(stage: str):
        if stage not in STAGES:
            raise ValueError("Unknown job stage")

    @staticmethod
    def _clock(now: float | None) -> float:
        value = time.time() if now is None else float(now)
        if not math.isfinite(value):
            raise ValueError("Job timestamp must be finite")
        return value

    def _insert_job(self, call_id: str, stage: str, priority: int) -> int:
        self.connection.execute("INSERT INTO jobs (call_id,stage,state,priority) VALUES (?,?,'ready',?) ON CONFLICT(call_id,stage) DO NOTHING", (call_id, stage, priority))
        return self.connection.execute("SELECT id FROM jobs WHERE call_id=? AND stage=?", (call_id, stage)).fetchone()[0]

    def enqueue_call(self, call: dict, stage: str, priority: int = 0) -> int:
        self._stage(stage)
        if priority not in (0, 1):
            raise ValueError("Job priority must be 0 or 1")
        with self._transaction():
            existing = self.connection.execute("SELECT id FROM jobs WHERE call_id=? AND stage=?", (call["id"], stage)).fetchone()
            if existing:
                return existing[0]
            self._save({**call, "status": WAITING_STATUS[stage]})
            return self._insert_job(call["id"], stage, priority)

    def _release_rows(self, rows) -> int:
        for row in rows:
            self.connection.execute("UPDATE jobs SET state='ready',available_at=0,lease_owner=NULL,lease_token=NULL,lease_until=NULL WHERE id=?", (row["id"],))
            call = self.get(row["call_id"])
            if call:
                self._save({**call, "status": WAITING_STATUS[row["stage"]], "error": None})
        return len(rows)

    def _recover_expired(self, now: float) -> int:
        rows = self.connection.execute("SELECT id,call_id,stage FROM jobs WHERE state='running' AND lease_until<=?", (now,)).fetchall()
        return self._release_rows(rows)

    def recover_expired(self, now: float | None = None) -> int:
        current = self._clock(now)
        with self._transaction():
            return self._recover_expired(current)

    def recover_all_jobs(self) -> int:
        """Call after acquiring the process lock; interrupted workers are then gone."""
        with self._transaction():
            return self._release_rows(self.connection.execute("SELECT id,call_id,stage FROM jobs WHERE state='running'").fetchall())

    def release_jobs(self, owner: str) -> int:
        with self._transaction():
            return self._release_rows(self.connection.execute("SELECT id,call_id,stage FROM jobs WHERE state='running' AND lease_owner=?", (owner,)).fetchall())

    def reset_failed_jobs(self, stage: str | None = None) -> int:
        """Explicit offline operator retry; completed and active work is unchanged."""
        if stage is not None:
            self._stage(stage)
        with self._transaction():
            rows = self.connection.execute("SELECT id,call_id,stage FROM jobs WHERE state='failed' AND (? IS NULL OR stage=?)", (stage, stage)).fetchall()
            for row in rows:
                self.connection.execute("UPDATE jobs SET state='ready',attempts=0,available_at=0,lease_owner=NULL,lease_token=NULL,lease_until=NULL,last_error=NULL WHERE id=?", (row["id"],))
                call = self.get(row["call_id"])
                if call:
                    self._save({**call, "status": WAITING_STATUS[row["stage"]], "error": None})
            return len(rows)

    def claim_job(self, stage: str, owner: str, lease_seconds: float = 300, now: float | None = None) -> dict | None:
        self._stage(stage)
        current = self._clock(now)
        if not owner or not isinstance(owner, str):
            raise ValueError("Job owner is required")
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("Job lease must be positive and finite")
        with self._transaction():
            self._recover_expired(current)
            row = self.connection.execute("""SELECT jobs.*,
                CASE WHEN jobs.priority=0 AND calls.started>? THEN 0 ELSE 1 END AS effective_priority
                FROM jobs JOIN calls ON calls.id=jobs.call_id
                WHERE jobs.stage=? AND jobs.state IN ('ready','retry') AND jobs.available_at<=?
                ORDER BY effective_priority,calls.started,jobs.id LIMIT 1""", (current - self.live_job_age_seconds, stage, current)).fetchone()
            if row is None:
                return None
            token = uuid4().hex
            until = current + lease_seconds
            self.connection.execute("UPDATE jobs SET state='running',lease_owner=?,lease_token=?,lease_until=? WHERE id=?", (owner, token, until, row["id"]))
            call = self.get(row["call_id"])
            self._save({**call, "status": RUNNING_STATUS[stage], "error": None})
            return {"id": row["id"], "call_id": row["call_id"], "stage": stage, "priority": row["effective_priority"], "attempts": row["attempts"], "lease_token": token, "lease_until": until}

    def _leased_job(self, job_id: int, lease_token: str):
        return self.connection.execute("SELECT * FROM jobs WHERE id=? AND state='running' AND lease_token=?", (job_id, lease_token)).fetchone()

    def finish_job(self, job_id: int, lease_token: str, call: dict, next_stage: str | None = None) -> bool:
        if next_stage is not None:
            self._stage(next_stage)
        with self._transaction():
            job = self._leased_job(job_id, lease_token)
            if job is None:
                return False
            if call["id"] != job["call_id"]:
                raise ValueError("Job and call do not match")
            updated = {**call, "status": WAITING_STATUS[next_stage]} if next_stage else call
            self._save(updated)
            self.connection.execute("UPDATE jobs SET state='done',lease_owner=NULL,lease_token=NULL,lease_until=NULL,last_error=NULL WHERE id=?", (job_id,))
            if next_stage:
                self._insert_job(call["id"], next_stage, job["priority"])
            return True

    def retry_job(self, job_id: int, lease_token: str, call: dict, error: str, retryable: bool = True, max_attempts: int = 4, delay_seconds: float = 5, now: float | None = None) -> bool:
        current = self._clock(now)
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("Maximum attempts must be positive")
        if not math.isfinite(delay_seconds) or not 0 <= delay_seconds <= 86400:
            raise ValueError("Retry delay must be finite and bounded")
        if not isinstance(error, str):
            raise TypeError("Job error must be text")
        with self._transaction():
            job = self._leased_job(job_id, lease_token)
            if job is None:
                return False
            if call["id"] != job["call_id"]:
                raise ValueError("Job and call do not match")
            attempts = job["attempts"] + 1
            should_retry = retryable and attempts < max_attempts
            status = WAITING_STATUS[job["stage"]] if should_retry else "failed"
            self._save({**call, "status": status, "error": error[:200]})
            self.connection.execute("UPDATE jobs SET state=?,attempts=?,available_at=?,lease_owner=NULL,lease_token=NULL,lease_until=NULL,last_error=? WHERE id=?", ("retry" if should_retry else "failed", attempts, current + delay_seconds, error[:200], job_id))
            return True

    def job_counts(self, now: float | None = None) -> dict:
        current = self._clock(now)
        result = {stage: {key: 0 for key in ("ready", "running", "retry", "failed", "live", "history")} for stage in STAGES}
        with self.lock:
            rows = self.connection.execute("""SELECT stage,state,COUNT(*) AS total,
                SUM(CASE WHEN priority=0 AND calls.started>? THEN 1 ELSE 0 END) AS live
                FROM jobs JOIN calls ON calls.id=jobs.call_id WHERE state!='done' GROUP BY stage,state""", (current - self.live_job_age_seconds,)).fetchall()
        for row in rows:
            bucket = result[row["stage"]]
            bucket[row["state"]] = row["total"]
            if row["state"] != "failed":
                bucket["live"] += row["live"]
                bucket["history"] += row["total"] - row["live"]
        return result

    def has_jobs(self) -> bool:
        with self.lock:
            return self.connection.execute("SELECT 1 FROM jobs LIMIT 1").fetchone() is not None

    def _recover_legacy_calls(self) -> int:
        rows = self.connection.execute("""SELECT calls.id,calls.document FROM calls
            WHERE status IN ('recording','queued','transcribing','translation_pending','translating')
            AND NOT EXISTS (SELECT 1 FROM jobs WHERE jobs.call_id=calls.id)""").fetchall()
        recovered = 0
        for row in rows:
            call = json.loads(row["document"])
            if call.get("duration", 0) < self.min_duration:
                continue
            stage = "translation" if call.get("original") else "transcription"
            self._insert_job(row["id"], stage, 1)
            recovered += 1
        return recovered

    def recover_legacy_calls(self) -> int:
        with self._transaction():
            return self._recover_legacy_calls()

    def get_setting(self, name: str, default=None):
        with self.lock:
            row = self.connection.execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, name: str, value):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with self._transaction():
            self.connection.execute("INSERT INTO settings (name,value) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (name, encoded))

    def backup_to(self, destination: Path) -> Path:
        with self.lock:
            return backup_database(self.connection, Path(destination))

    def backupTo(self, destination: Path) -> Path:
        return self.backup_to(destination)

    def close(self, checkpoint: bool = True):
        with self.lock:
            if self._closed:
                return
            try:
                if checkpoint:
                    self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                self.connection.close()
                self._closed = True
