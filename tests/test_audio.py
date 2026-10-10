import wave
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.audio import (
    audio_path,
    migrate_recordings,
    recording_name,
    write_recording,
)
from backend.store import Store


def call(**changes):
    return {
        "id": "a" * 32,
        "started_at": datetime(2026, 10, 8, 3, 15, 20, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp(),
        "slot": 2,
        "source_id": 1234567,
        "callsign": "TEST1",
        **changes,
    }


def test_filename_uses_beijing_time_slot_sender_id_and_callsign():
    assert recording_name(call()) == "20261008/03-15-20_TS2_1234567_TEST1.wav"


def test_only_complete_mono_8khz_wav_is_a_valid_recording(tmp_path):
    from backend.audio import valid_recording
    name = write_recording(tmp_path, call(), bytes(16000))
    path = tmp_path / name
    assert valid_recording(path)
    path.write_bytes(path.read_bytes()[:50])
    assert not valid_recording(path)


def test_unknown_and_unsafe_callsigns_have_safe_filenames():
    assert recording_name(call(callsign="1234567")).endswith("_unknown.wav")
    assert recording_name(call(callsign="../../TEST1\x00\n")).endswith("_TEST1.wav")
    assert recording_name(call(callsign="DL1ABC/P")).endswith("_DL1ABC-P.wav")


def test_same_second_calls_do_not_overwrite_each_others_audio(tmp_path):
    first = write_recording(tmp_path, call(), b"\x01\x00" * 8000)
    second = write_recording(tmp_path, call(id="b" * 32), b"\x02\x00" * 8000)
    assert first == "20261008/03-15-20_TS2_1234567_TEST1.wav"
    assert second == "20261008/03-15-20_TS2_1234567_TEST1-02.wav"
    with wave.open(str(tmp_path / first), "rb") as wav:
        assert wav.getframerate() == 8000
        assert wav.readframes(8000) == b"\x01\x00" * 8000


def test_path_lookup_supports_both_named_and_legacy_audio(tmp_path):
    assert audio_path(tmp_path, call()) == tmp_path / (("a" * 32) + ".wav")
    name = recording_name(call())
    assert audio_path(tmp_path, call(audio_filename=name)) == tmp_path / name


@pytest.mark.parametrize("name", ["../secret.wav", "/tmp/secret.wav", "sub/call.wav", "sub\\call.wav", "call.wav\x00", "call.txt", "20261308/call.wav", "20261008/../call.wav", "20261008/sub/call.wav"])
def test_audio_paths_cannot_escape_recording_directory(tmp_path, name):
    with pytest.raises(ValueError):
        audio_path(tmp_path, call(audio_filename=name))


def test_audio_symlinks_cannot_escape_recording_directory(tmp_path):
    directory = tmp_path / "audio"
    directory.mkdir()
    target = tmp_path / "outside.wav"
    target.write_bytes(b"outside")
    (directory / "call.wav").symlink_to(target)
    with pytest.raises(ValueError):
        audio_path(directory, call(audio_filename="call.wav"))


def test_migration_preserves_bytes_and_handles_same_second_collisions(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    directory = tmp_path / "audio"
    directory.mkdir()
    first, second = call(status="complete"), call(id="b" * 32, status="complete")
    for record, content in [(first,b"first"), (second,b"second")]:
        store.save(record)
        (directory / (record["id"] + ".wav")).write_bytes(content)
    result = migrate_recordings(store, directory)
    assert len(result["renamed"]) == 2
    assert audio_path(directory, store.get(first["id"])).read_bytes() == b"first"
    assert audio_path(directory, store.get(second["id"])).read_bytes() == b"second"
    assert store.get(second["id"])["audio_filename"].endswith("-02.wav")
    assert not (directory / (first["id"] + ".wav")).exists()
    assert migrate_recordings(store, directory)["unchanged"] == 2
    store.close()


def test_interrupted_migration_can_resume_without_losing_audio(tmp_path, monkeypatch):
    store = Store(tmp_path / "calls.sqlite3")
    directory = tmp_path / "audio"
    directory.mkdir()
    record = call(status="complete")
    store.save(record)
    legacy = directory / (record["id"] + ".wav")
    legacy.write_bytes(b"recording")
    original_save = store.save

    def fail_save(record):
        raise RuntimeError("simulated interruption before database commit")

    monkeypatch.setattr(store, "save", fail_save)
    with pytest.raises(RuntimeError):
        migrate_recordings(store, directory)
    assert legacy.read_bytes() == b"recording"
    assert (directory / recording_name(record)).read_bytes() == b"recording"
    monkeypatch.setattr(store, "save", original_save)
    assert len(migrate_recordings(store, directory)["renamed"]) == 1
    assert audio_path(directory, store.get(record["id"])).read_bytes() == b"recording"
    store.close()


def test_migration_keeps_legacy_audio_when_metadata_is_missing(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    directory = tmp_path / "audio"
    directory.mkdir()
    record = {"id":"a" * 32, "started_at":100, "status":"complete"}
    store.save(record)
    legacy = directory / (record["id"] + ".wav")
    legacy.write_bytes(b"recording")
    assert migrate_recordings(store, directory)["skipped"] == [record["id"]]
    assert legacy.read_bytes() == b"recording"
    store.close()


def test_migration_does_not_replace_audio_with_a_dangling_symlink(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    directory = tmp_path / "audio"
    directory.mkdir()
    record = call(status="complete")
    store.save(record)
    legacy = directory / (record["id"] + ".wav")
    legacy.write_bytes(b"recording")
    target = directory / recording_name(record)
    target.parent.mkdir()
    target.symlink_to(legacy)
    migrate_recordings(store, directory)
    path = audio_path(directory, store.get(record["id"]))
    assert not path.is_symlink()
    assert path.read_bytes() == b"recording"
    store.close()


def test_date_directory_symlinks_cannot_escape_audio_root(tmp_path):
    directory = tmp_path / "audio"
    directory.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (directory / "20261008").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        write_recording(directory, call(), bytes(16000))


def test_migration_also_moves_existing_flat_descriptive_names(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    directory = tmp_path / "audio"
    directory.mkdir()
    record = call(status="complete", audio_filename="2026-10-08_03-15-20_TS2_1234567_TEST1.wav")
    store.save(record)
    (directory / record["audio_filename"]).write_bytes(b"recording")
    assert len(migrate_recordings(store, directory)["renamed"]) == 1
    path = audio_path(directory, store.get(record["id"]))
    assert path.parent.name == "20261008"
    assert path.name == "03-15-20_TS2_1234567_TEST1.wav"
    assert path.read_bytes() == b"recording"
    store.close()


def test_migration_resumes_after_db_commit_before_removing_old_name(tmp_path, monkeypatch):
    from pathlib import Path
    store = Store(tmp_path / "calls.sqlite3")
    directory = tmp_path / "audio"
    directory.mkdir()
    record = call(status="complete")
    store.save(record)
    legacy = directory / (record["id"] + ".wav")
    legacy.write_bytes(b"recording")
    original_unlink = Path.unlink

    def fail_unlink(path, *args, **kwargs):
        raise RuntimeError("simulated interruption after database commit")

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(RuntimeError):
        migrate_recordings(store, directory)
    assert audio_path(directory, store.get(record["id"])).read_bytes() == b"recording"
    monkeypatch.setattr(Path, "unlink", original_unlink)
    assert len(migrate_recordings(store, directory)["renamed"]) == 1
    assert not legacy.exists()
    assert "_audio_migration_source" not in store.get(record["id"])
    store.close()


def test_migration_recovery_rejects_symlink_target_without_removing_source(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    directory = tmp_path / "audio"
    directory.mkdir()
    record = call(status="complete", audio_filename=recording_name(call()), _audio_migration_source="a" * 32 + ".wav")
    store.save(record)
    legacy = directory / record["_audio_migration_source"]
    legacy.write_bytes(b"recording")
    target = directory / record["audio_filename"]
    target.parent.mkdir()
    target.symlink_to(legacy)
    assert migrate_recordings(store, directory)["skipped"] == [record["id"]]
    assert legacy.read_bytes() == b"recording"
    store.close()
