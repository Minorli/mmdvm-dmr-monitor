from backend.store import Store


def test_replaces_a_call_and_preserves_started_time_order(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    store.save({"id":"a", "started_at":100, "status":"recording", "source_id":1})
    store.save({"id":"b", "started_at":200, "status":"queued", "source_id":2})
    store.save({"id":"a", "started_at":100, "status":"complete", "original":"hello"})
    assert [call["id"] for call in store.list_calls()] == ["b", "a"]
    assert store.get("a")["original"] == "hello"
    assert store.counts() == {"calls":2,"translated":1}
    store.close()


def test_pending_recovery_is_not_limited_by_recent_call_history(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    store.save({"id":"old", "started_at":1, "status":"queued"})
    for number in range(205):
        store.save({"id":str(number), "started_at":number+2, "status":"complete"})
    assert "old" not in [item["id"] for item in store.list_calls()]
    assert [item["id"] for item in store.pending_calls()] == ["old"]
    store.close()


def test_short_history_is_hidden_from_list_and_counts(tmp_path):
    store = Store(tmp_path / "test.sqlite3", min_duration=1)
    for name, duration, status in [("short", .3, "complete"), ("no_speech", 4, "short"), ("discarded", 4, "discarded"), ("valid", 1, "complete")]:
        store.save({"id":name, "started_at":100, "duration":duration, "status":status})
    assert [item["id"] for item in store.list_calls()] == ["valid"]
    assert store.counts() == {"calls":1, "translated":1}
    store.close()


def test_listening_selection_persists_without_implying_a_default_start(tmp_path):
    path = tmp_path / "calls.sqlite3"
    store = Store(path)
    assert store.get_setting("listening_requested") is None
    assert store.get_setting("listening_requested", False) is False
    store.set_setting("listening_requested", True)
    store.close()
    store = Store(path)
    assert store.get_setting("listening_requested") is True
    store.set_setting("listening_requested", False)
    assert store.get_setting("listening_requested") is False
    store.close()


def test_settings_version_upgrade_keeps_jobs_and_backs_up_before_change(tmp_path):
    import sqlite3
    path = tmp_path / "calls.sqlite3"
    store = Store(path)
    store.enqueue_call({"id":"existing", "started_at":100, "status":"queued", "duration":1}, "transcription")
    store.close()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE settings")
        connection.execute("PRAGMA user_version=1")
    upgraded = Store(path)
    assert upgraded.schema_version == 2
    assert upgraded.connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert upgraded.job_counts()["transcription"]["ready"] == 1
    assert upgraded.get("existing")["duration"] == 1
    assert upgraded.migration_backup.is_file()
    with sqlite3.connect(upgraded.migration_backup) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
        assert backup.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert backup.execute("SELECT name FROM sqlite_master WHERE name='settings'").fetchone() is None
    upgraded.close()


def test_settings_support_json_values_and_parameterized_names(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    name = "choice'); DROP TABLE calls; --"
    value = {"chosen": False, "label": "待机", "items": [1, None]}
    store.set_setting(name, value)
    assert store.get_setting(name) == value
    assert store.counts() == {"calls":0, "translated":0}
    store.close()


def test_patching_audio_fields_keeps_fresh_transcript_and_identity(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    store.save({"id":"a", "started_at":100, "status":"complete", "original":"first", "callsign":"unknown", "audio_url":"/api/audio/a"})
    store.save({**store.get("a"), "original":"new transcript", "callsign":"TEST1"})
    updated = store.patch_call("a", {"audio_url":None, "audio_deleted":True})
    assert updated["original"] == "new transcript"
    assert updated["callsign"] == "TEST1"
    assert updated["audio_url"] is None
    assert updated["audio_deleted"] is True
    assert store.patch_call("missing", {"audio_url":None}) is None
    store.close()


def test_latest_tg_uses_each_slots_full_history_and_skips_missing_values(tmp_path):
    store = Store(tmp_path / "calls.sqlite3")
    store.save({"id":"known", "started_at":1, "slot":2, "tg":12345, "status":"discarded"})
    store.save({"id":"missing", "started_at":2, "slot":2, "status":"complete"})
    assert store.latest_tg(2) == 12345
    assert store.latest_tg(1) == 0
    store.close()
