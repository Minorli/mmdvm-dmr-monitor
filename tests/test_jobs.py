import json
import sqlite3

import pytest

from backend.store import Store


def call(number=1, started_at=1000, **changes):
    return {
        "id": f"{number:032x}", "started_at": started_at, "duration": 2,
        "status": "queued", "source_id": number, "original": None,
        "translated": None, "error": None, **changes,
    }


def test_more_than_120_jobs_survive_reopening_without_becoming_failed(tmp_path):
    path = tmp_path / "calls.sqlite3"
    store = Store(path)
    for number in range(150):
        store.enqueue_call(call(number), "transcription")
    store.close()
    store = Store(path)
    assert store.job_counts()["transcription"]["ready"] == 150
    assert all(record["status"] == "queued" for record in store.list_calls(200))
    store.close()


def test_enqueue_is_idempotent_and_does_not_regress_completed_call(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    record = call()
    first = store.enqueue_call(record, "transcription")
    assert store.enqueue_call(record, "transcription") == first
    claimed = store.claim_job("transcription", "worker", now=1000)
    assert store.finish_job(claimed["id"], claimed["lease_token"], {**record, "status": "complete"})
    assert store.enqueue_call(record, "transcription") == first
    assert store.get(record["id"])["status"] == "complete"
    assert store.claim_job("transcription", "worker", now=1000) is None
    store.close()


def test_claim_is_exclusive_between_connections_and_stop_does_not_count_a_failure(tmp_path):
    path = tmp_path / "calls.sqlite3"
    first, second = Store(path), Store(path)
    record = call()
    first.enqueue_call(record, "transcription")
    claimed = first.claim_job("transcription", "first", now=1000)
    assert claimed["attempts"] == 0
    assert second.claim_job("transcription", "second", now=1000) is None
    assert first.release_jobs("first") == 1
    retried = second.claim_job("transcription", "second", now=1000)
    assert retried["attempts"] == 0
    assert retried["lease_token"] != claimed["lease_token"]
    assert not first.finish_job(claimed["id"], claimed["lease_token"], {**record, "status": "complete"})
    assert second.get(record["id"])["status"] == "transcribing"
    first.close()
    second.close()


def test_expired_and_abandoned_leases_are_recoverable(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    store.enqueue_call(call(), "transcription")
    original = store.claim_job("transcription", "gone", lease_seconds=10, now=1000)
    assert store.recover_expired(now=1009) == 0
    assert store.recover_expired(now=1010) == 1
    claimed = store.claim_job("transcription", "new", now=1010)
    assert claimed["lease_token"] != original["lease_token"]
    assert store.recover_all_jobs() == 1
    assert store.job_counts()["transcription"]["ready"] == 1
    store.close()


def test_live_jobs_precede_history_and_old_live_jobs_age_into_history(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    ancient = call(1, started_at=100)
    historical = call(2, started_at=110)
    live = call(3, started_at=990)
    store.enqueue_call(ancient, "transcription", priority=0)
    store.enqueue_call(historical, "transcription", priority=1)
    store.enqueue_call(live, "transcription", priority=0)
    claimed = store.claim_job("transcription", "worker", now=1000)
    assert claimed["call_id"] == live["id"]
    assert claimed["priority"] == 0
    assert store.finish_job(claimed["id"], claimed["lease_token"], {**live, "status": "complete"})
    assert store.claim_job("transcription", "worker", now=1000)["priority"] == 1
    store.close()


def test_configured_live_age_applies_to_both_claim_order_and_metrics(tmp_path):
    store = Store(tmp_path / "calls.sqlite3", live_job_age_seconds=5)
    old_live, new_live = call(1, started_at=990), call(2, started_at=999)
    store.enqueue_call(old_live, "transcription")
    store.enqueue_call(new_live, "transcription")
    counts = store.job_counts(now=1000)["transcription"]
    assert (counts["live"], counts["history"]) == (1, 1)
    assert store.claim_job("transcription", "worker", now=1000)["call_id"] == new_live["id"]
    store.close()


def test_finishing_transcription_enqueues_translation_atomically(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    record = call()
    store.enqueue_call(record, "transcription")
    claimed = store.claim_job("transcription", "worker", now=1000)
    completed = {**record, "original": "hello", "language": "en"}
    assert store.finish_job(claimed["id"], claimed["lease_token"], completed, next_stage="translation")
    assert store.get(record["id"])["status"] == "translation_pending"
    next_job = store.claim_job("translation", "translator", now=1000)
    assert next_job["call_id"] == record["id"]
    assert store.get(record["id"])["original"] == "hello"
    assert store.finish_job(next_job["id"], next_job["lease_token"], {**completed, "translated": "你好", "status": "complete"})
    assert store.job_counts()["translation"]["running"] == 0
    store.close()


def test_chain_failure_rolls_back_call_and_completed_job(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    record = call()
    store.enqueue_call(record, "transcription")
    claimed = store.claim_job("transcription", "worker", now=1000)
    store.connection.execute("CREATE TRIGGER reject_translation BEFORE INSERT ON jobs WHEN NEW.stage='translation' BEGIN SELECT RAISE(ABORT, 'rejected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.finish_job(claimed["id"], claimed["lease_token"], {**record, "original": "hello"}, next_stage="translation")
    assert store.get(record["id"])["status"] == "transcribing"
    assert store.get(record["id"])["original"] is None
    assert store.job_counts()["transcription"]["running"] == 1
    store.close()


def test_retry_deadline_failure_limit_and_translation_status_are_persisted(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    record = call(original="hello")
    store.enqueue_call(record, "translation")
    claimed = store.claim_job("translation", "worker", now=1000)
    assert store.retry_job(claimed["id"], claimed["lease_token"], record, "HTTP 503", max_attempts=2, delay_seconds=10, now=1000)
    assert store.get(record["id"])["status"] == "translation_pending"
    assert store.claim_job("translation", "worker", now=1009) is None
    claimed = store.claim_job("translation", "worker", now=1010)
    assert claimed["attempts"] == 1
    assert store.retry_job(claimed["id"], claimed["lease_token"], record, "HTTP 503", max_attempts=2, delay_seconds=10, now=1010)
    assert store.get(record["id"])["status"] == "failed"
    assert store.claim_job("translation", "worker", now=9999) is None
    assert store.job_counts()["translation"]["failed"] == 1
    store.close()


def test_non_retryable_failure_is_terminal_and_released_worker_cannot_fail_it(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    record = call()
    store.enqueue_call(record, "transcription")
    stale = store.claim_job("transcription", "old", now=1000)
    store.release_jobs("old")
    claimed = store.claim_job("transcription", "new", now=1000)
    assert not store.retry_job(stale["id"], stale["lease_token"], record, "old error", now=1000)
    assert store.retry_job(claimed["id"], claimed["lease_token"], record, "missing audio", retryable=False, now=1000)
    assert store.get(record["id"])["error"] == "missing audio"
    assert store.job_counts()["transcription"]["failed"] == 1
    store.close()


def test_interrupted_recording_atomically_invalidates_all_active_job_leases(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    record = call(audio_url="/api/audio/old", original="hello")
    store.enqueue_call(record, "transcription")
    store.enqueue_call(record, "translation")
    stale = store.claim_job("transcription", "old", now=1000)
    store.fail_call(record, "上次运行中断，没有完成录音")
    failed = store.get(record["id"])
    assert failed["status"] == "failed"
    assert failed["audio_url"] is None
    assert failed["error"] == "上次运行中断，没有完成录音"
    assert store.job_counts()["transcription"]["failed"] == 1
    assert store.job_counts()["translation"]["failed"] == 1
    assert not store.finish_job(stale["id"], stale["lease_token"], {**record, "status":"complete"})
    assert store.claim_job("translation", "worker", now=1000) is None
    store.close()


def test_offline_retry_resets_only_failed_jobs_in_the_requested_stage(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    failed_asr, failed_translation, completed = call(1), call(2, original="hello"), call(3)
    for record, stage in [(failed_asr,"transcription"), (failed_translation,"translation"), (completed,"transcription")]:
        store.enqueue_call(record, stage)
        claimed = store.claim_job(stage, "worker", now=1000)
        if record is completed:
            store.finish_job(claimed["id"], claimed["lease_token"], {**record, "status":"complete"})
        else:
            store.retry_job(claimed["id"], claimed["lease_token"], record, "failed", retryable=False, now=1000)
    assert store.reset_failed_jobs("translation") == 1
    translation = store.claim_job("translation", "worker", now=1000)
    assert translation["attempts"] == 0
    assert store.get(failed_translation["id"])["error"] is None
    assert store.get(completed["id"])["status"] == "complete"
    assert store.job_counts()["transcription"]["failed"] == 1
    assert store.reset_failed_jobs() == 1
    assert store.claim_job("transcription", "worker", now=1000)["call_id"] == failed_asr["id"]
    store.close()


def test_legacy_migration_backs_up_documents_and_recovers_each_pending_stage(tmp_path):
    path = tmp_path / "calls.sqlite3"
    records = [call(1), call(2, status="translating", original="hello"), call(3, status="complete"), call(4, status="discarded")]
    with sqlite3.connect(path) as legacy:
        legacy.execute("CREATE TABLE calls (id TEXT PRIMARY KEY, started REAL NOT NULL, status TEXT NOT NULL, document TEXT NOT NULL)")
        legacy.executemany("INSERT INTO calls VALUES (?, ?, ?, ?)", [(item["id"], item["started_at"], item["status"], json.dumps(item)) for item in records])
    store = Store(path)
    assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert store.migration_backup.is_file()
    with sqlite3.connect(store.migration_backup) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 0
        assert backup.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 4
    assert store.get(records[0]["id"]) == records[0]
    assert store.job_counts()["transcription"]["ready"] == 1
    assert store.job_counts()["translation"]["ready"] == 1
    assert store.recover_legacy_calls() == 0
    store.close()
    reopened = Store(path)
    assert reopened.migration_backup is None
    assert reopened.job_counts()["transcription"]["ready"] == 1
    reopened.close()


def test_online_backup_keeps_completed_job_state(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    store.enqueue_call(call(), "transcription")
    destination = tmp_path / "snapshot.sqlite3"
    assert store.backup_to(destination) == destination
    with sqlite3.connect(destination) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT COUNT(*) FROM jobs WHERE state='ready'").fetchone()[0] == 1
    with pytest.raises(FileExistsError):
        store.backup_to(destination)
    store.close()


def test_unknown_future_schema_is_rejected_without_downgrading(tmp_path):
    path = tmp_path / "calls.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="schema"):
        Store(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 999
