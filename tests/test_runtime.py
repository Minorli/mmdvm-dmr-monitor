import asyncio
import time

import httpx

from backend.audio import audio_path
from backend.config import Settings
from backend.runtime import Runtime
from backend.sessions import Segment
from backend.store import Store


def configuration(tmp_path):
    return Settings(tmp_path, "pi-star@radio.local", "https://api.example.test", "", "test", "small", 8788)


def test_each_slot_restores_its_last_tg_even_outside_the_recent_100_calls(tmp_path):
    store = Store(tmp_path / "calls.sqlite3", min_duration=1)
    store.save({"id":"older-ts2", "started_at":1, "slot":2, "tg":46000, "status":"complete", "duration":1})
    store.save({"id":"latest-ts2", "started_at":2, "slot":2, "tg":46001, "status":"complete", "duration":1})
    for number in range(150):
        store.save({"id":f"ts1-{number}", "started_at":number+100, "slot":1, "tg":91, "status":"complete", "duration":1})
    assert all(call["slot"] == 1 for call in store.list_calls(100))
    store.close()
    runtime = Runtime(configuration(tmp_path))
    assert {slot["slot"]:slot["tg"] for slot in runtime.status()["slots"]} == {1:91, 2:46001}
    runtime.store.close()


def test_no_history_leaves_both_slots_without_a_known_tg(tmp_path):
    runtime = Runtime(configuration(tmp_path))
    assert [slot["tg"] for slot in runtime.status()["slots"]] == [0, 0]
    runtime.store.close()


def test_status_versions_order_control_responses_and_delayed_pushes(tmp_path, monkeypatch):
    runtime = Runtime(configuration(tmp_path))
    monkeypatch.setattr("backend.runtime.time.time_ns", lambda: 1_791_590_000_000_000_000)
    runtime.listening_state = "running"
    delayed_push = runtime.status()
    runtime.listening_state = "stopped"
    control_response = runtime.status()
    assert control_response["status_version"] > delayed_push["status_version"]
    assert control_response["status_version"] < 2**53
    assert delayed_push["listening_state"] == "running"
    assert control_response["listening_state"] == "stopped"
    # Clock adjustments must not reverse status ordering within this process.
    monkeypatch.setattr("backend.runtime.time.time_ns", lambda: 0)
    assert runtime.status()["status_version"] > control_response["status_version"]
    runtime.store.close()


def test_failed_directory_lookup_can_be_retried_on_later_calls(tmp_path):
    runtime = Runtime(configuration(tmp_path))

    async def failure(source_id):
        raise httpx.ConnectError("directory temporarily offline")

    runtime.directory.lookup = failure

    async def run():
        runtime.lookup_tasks[2353660] = asyncio.current_task()
        await runtime.enrich(2353660)
        assert 2353660 not in runtime.lookup_tasks

    asyncio.run(run())
    runtime.store.close()


def test_live_audio_has_priority_over_old_recovered_audio(tmp_path):
    runtime = Runtime(configuration(tmp_path))
    now = time.time()
    previous = Segment(1, 1, 91, "old", "incoming", now-3600, now-3597, pcm=bytes(48000), ended_at=now-3597)
    runtime.finish(previous)

    async def run():
        await runtime.start()
        try:
            assert runtime.listening_state == "stopped"
            assert not runtime.pipeline.running
            live = Segment(2, 2, 46001, "live", "incoming", now, now+3, pcm=bytes(48000), ended_at=now+3)
            runtime.finish(live)
            assert runtime.store.claim_job("transcription", "test")["call_id"] == live.id
            assert runtime.store.claim_job("transcription", "test")["call_id"] == previous.id
        finally:
            runtime.store.release_jobs("test")
            await runtime.stop()

    asyncio.run(run())


def test_short_transmissions_create_no_cards_recordings_or_jobs(tmp_path):
    runtime = Runtime(configuration(tmp_path))
    short = Segment(2, 4615277, 46001, "short", "incoming", 100, 100.3, pcm=bytes(4800), ended_at=100.3)
    runtime.finish(short)
    assert runtime.store.list_calls() == []
    assert not runtime.store.has_jobs()
    assert not (tmp_path / "audio").exists()
    runtime.store.close()


def test_one_second_call_is_retained_and_shown_only_after_threshold(tmp_path):
    from tests.test_packets import frame
    runtime = Runtime(configuration(tmp_path))
    runtime.directory.cache["2353660"] = {"callsign":"MM7MVG", "name":"", "country":"英国", "country_code":"GB"}

    class Decoder:
        def reset(self): pass
        def decode(self, burst): return bytes(960)

    runtime.decoders[(1, "incoming")] = Decoder()
    for sequence in range(16):
        runtime.handle_packet(frame(sequence=sequence), 100 + sequence * .06, "incoming")
    assert runtime.store.list_calls() == []
    assert not runtime.status()["slots"][0]["active"]
    runtime.handle_packet(frame(sequence=16), 101, "incoming")
    assert len(runtime.store.list_calls()) == 1
    assert runtime.status()["slots"][0]["active"]
    exact_one_second = Segment(2, 2353660, 46001, "one", "incoming", 100, 101, pcm=bytes(16000), ended_at=101)
    runtime.finish(exact_one_second)
    record = runtime.store.get(exact_one_second.id)
    assert record["status"] == "queued"
    assert audio_path(tmp_path / "audio", record).is_file()
    assert runtime.store.job_counts()["transcription"]["ready"] == 1
    runtime.store.close()


def test_stop_preserves_recording_and_durable_job_without_starting_processing(tmp_path):
    runtime = Runtime(configuration(tmp_path))
    segment = Segment(1, 1, 91, "voice", "incoming", 100, 101, pcm=bytes(16000), ended_at=101)
    runtime.finish(segment)

    async def run():
        await runtime.start()
        assert runtime.pipeline.metrics()["transcription"] == "paused"
        assert runtime.store.get(segment.id)["status"] == "queued"
        await runtime.stop()

    asyncio.run(run())
    reopened = Runtime(configuration(tmp_path))
    record = reopened.store.get(segment.id)
    assert audio_path(tmp_path / "audio", record).is_file()
    assert reopened.store.job_counts()["transcription"]["ready"] == 1
    reopened.store.close()


def test_hard_exit_during_recording_does_not_leave_a_fake_active_call(tmp_path):
    runtime = Runtime(configuration(tmp_path))
    segment = Segment(1, 1, 91, "voice", "incoming", 100, 101, pcm=bytes(16000))
    runtime.store.save(runtime.call_from_segment(segment))
    runtime.store.close()
    reopened = Runtime(configuration(tmp_path))

    async def run():
        await reopened.start()
        record = reopened.store.get(segment.id)
        assert record["status"] == "failed"
        assert record["audio_url"] is None
        assert "录音未完成" in record["error"]
        await reopened.stop()

    asyncio.run(run())
