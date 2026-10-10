import asyncio

from backend.config import Settings
from backend.recording_journal import RecordingJournal
from backend.runtime import Runtime
from backend.sessions import Segment


def test_snapshot_is_valid_audio_and_survives_process_reconstruction(tmp_path):
    segment = Segment(1, 1, 91, "stream", "incoming", 100, 103, pcm=b"\x01\x00" * 24000)
    RecordingJournal(tmp_path).snapshot(segment)
    reopened = RecordingJournal(tmp_path)
    assert reopened.recover(segment.id) == segment.pcm
    reopened.discard(segment.id)
    assert reopened.recover(segment.id) is None


def test_hard_exit_restores_saved_partial_audio_into_durable_queue(tmp_path):
    cfg = Settings(tmp_path, "", "https://api.example.test", "", "test", "small", 8788)
    first = Runtime(cfg)
    segment = Segment(2, 4610001, 46001, "stream", "incoming", 100, 103, pcm=b"\x01\x00" * 24000)
    first.store.save(first.call_from_segment(segment))
    first.journal.snapshot(segment)
    first.store.close()
    reopened = Runtime(cfg)

    async def run():
        await reopened.start()
        record = reopened.store.get(segment.id)
        assert record["status"] == "queued"
        assert record["duration"] == 3
        assert record["recovered_recording"]
        assert reopened.store.job_counts()["transcription"]["ready"] == 1
        assert reopened.listening_state == "stopped"
        await reopened.stop()

    asyncio.run(run())
