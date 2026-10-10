from backend.packets import parse_packet
from backend.sessions import SessionTracker
from tests.test_packets import frame


def packet(**kwargs):
    return parse_packet(frame(**kwargs))


def test_two_simultaneous_slots_do_not_share_audio_or_speakers():
    tracker = SessionTracker(max_seconds=15)
    tracker.feed(packet(), b"a" * 960, 100.0)
    tracker.feed(packet(slot=2, source=4615277, tg=46001), b"b" * 960, 100.01)
    completed = tracker.feed(packet(flags=0x22), b"", 100.2)
    assert len(completed) == 1
    assert completed[0].slot == 1
    assert completed[0].pcm == b"a" * 960
    assert tracker.active[(2, "incoming")].source_id == 4615277
    second = tracker.flush_expired(104)
    assert second[0].pcm == b"b" * 960


def test_retransmitted_voice_does_not_duplicate_audio():
    tracker = SessionTracker()
    tracker.feed(packet(), b"a" * 960, 100)
    tracker.feed(packet(), b"a" * 960, 100.1)
    result = tracker.flush_expired(104)
    assert result[0].duration == 0.06


def test_new_stream_finishes_previous_call():
    tracker = SessionTracker()
    tracker.feed(packet(), b"a" * 960, 100)
    result = tracker.feed(packet(stream=b"efgh", source=4040169), b"b" * 960, 101)
    assert result[0].source_id == 2353660
    assert tracker.active[(1, "incoming")].source_id == 4040169


def test_long_calls_are_split_without_dropping_trigger_frame():
    tracker = SessionTracker(max_seconds=0.06)
    tracker.feed(packet(sequence=1), b"a" * 960, 100)
    result = tracker.feed(packet(sequence=2), b"b" * 960, 100.06)
    assert result[0].pcm == b"a" * 960
    assert tracker.flush_expired(104)[0].pcm == b"b" * 960


def test_outgoing_and_incoming_have_separate_sessions():
    tracker = SessionTracker()
    tracker.feed(packet(), b"a" * 960, 100)
    tracker.feed(packet(), b"b" * 960, 100, direction="outgoing")
    results = tracker.flush_expired(104)
    assert sorted(item.pcm for item in results) == [b"a" * 960, b"b" * 960]


def test_late_end_from_previous_stream_does_not_stop_current_call():
    tracker = SessionTracker()
    tracker.feed(packet(), b"a" * 960, 100)
    tracker.feed(packet(stream=b"efgh", source=4040169), b"b" * 960, 101)
    assert tracker.feed(packet(flags=0x22), b"", 101.1) == []
    assert tracker.active[(1, "incoming")].source_id == 4040169


def test_retransmitted_voice_across_segment_boundary_is_dropped():
    tracker = SessionTracker(max_seconds=0.06)
    tracker.feed(packet(sequence=1), b"a" * 960, 100)
    tracker.feed(packet(sequence=2), b"b" * 960, 100.06)
    assert tracker.is_duplicate(packet(sequence=1))
    assert tracker.feed(packet(sequence=1), b"a" * 960, 100.07) == []
    assert tracker.flush_expired(104)[0].pcm == b"b" * 960


def test_valid_long_call_preserves_a_short_tail_with_audio_context():
    tracker = SessionTracker(max_seconds=1)
    tracker.feed(packet(sequence=1), b"a" * 16000, 100)
    first = tracker.feed(packet(sequence=2), b"b" * 960, 101)
    assert first[0].pcm == b"a" * 16000
    tail = tracker.feed(packet(flags=0x22), b"", 101.06)[0]
    assert tail.pcm == b"a" * 16000 + b"b" * 960
    assert tail.duration >= 1
