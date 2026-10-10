import time

import pytest

from backend.audio import write_recording
from backend.maintenance import audit_data, backup_data, prune_audio, restore_data
from backend.store import Store
from tests.test_audio import call


def prepared(tmp_path):
    directory = tmp_path / "data"
    store = Store(directory / "calls.sqlite3")
    record = call(status="complete", duration=1, audio_url="/api/audio/" + "a" * 32, original="hello", translated="你好")
    record["audio_filename"] = write_recording(directory / "audio", record, bytes(16000))
    store.save(record)
    store.close()
    return directory, record


def test_verified_backup_and_restore_preserve_text_and_audio(tmp_path):
    directory, record = prepared(tmp_path)
    destination = tmp_path / "backup"
    report = backup_data(directory, destination)
    assert report["verified"] and report["recordings"] == 1
    restored = tmp_path / "restored"
    restored.mkdir()
    restore_data(destination, restored)
    assert audit_data(restored)["missing_recordings"] == []
    store = Store(restored / "calls.sqlite3")
    assert store.get(record["id"])["translated"] == "你好"
    store.close()


def test_restore_rejects_corrupt_data_and_existing_directory(tmp_path):
    directory, record = prepared(tmp_path)
    destination = tmp_path / "backup"
    backup_data(directory, destination)
    restored = tmp_path / "restored"
    restored.mkdir()
    (destination / "audio" / record["audio_filename"]).write_bytes(b"damaged")
    with pytest.raises(ValueError):
        restore_data(destination, restored)


@pytest.mark.parametrize("missing", ["calls.sqlite3", "audio"])
def test_restore_rejects_incomplete_manifest_before_writing(tmp_path, missing):
    import json
    directory, _ = prepared(tmp_path)
    destination = tmp_path / "backup"
    backup_data(directory, destination)
    manifest_path = destination / "backup-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"] = [entry for entry in manifest["files"] if not entry["path"].startswith(missing)]
    manifest_path.write_text(json.dumps(manifest))
    restored = tmp_path / "restored"
    restored.mkdir()
    with pytest.raises(ValueError):
        restore_data(destination, restored)
    assert list(restored.iterdir()) == []
    (restored / "existing").write_text("keep")
    with pytest.raises(ValueError):
        restore_data(destination, restored)


def test_retention_is_disabled_by_default_and_never_removes_queued_audio(tmp_path):
    directory, record = prepared(tmp_path)
    store = Store(directory / "calls.sqlite3")
    assert prune_audio(store, directory, 0)["removed"] == 0
    store.enqueue_call({**record, "status":"queued"}, "transcription")
    assert prune_audio(store, directory, 1, now=time.time()+86400*60)["removed"] == 0
    assert (directory / "audio" / record["audio_filename"]).is_file()
    store.close()


def test_retention_removes_only_audio_and_keeps_transcript(tmp_path):
    directory, record = prepared(tmp_path)
    store = Store(directory / "calls.sqlite3")
    assert prune_audio(store, directory, 1, now=time.time()+86400*60)["removed"] == 1
    updated = store.get(record["id"])
    assert updated["audio_url"] is None
    assert updated["original"] == "hello"
    assert updated["translated"] == "你好"
    store.close()
