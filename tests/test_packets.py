import pytest

from backend.packets import parse_packet


def frame(slot=1, source=2353660, tg=91, flags=0x10, sequence=4, stream=b"abcd"):
    data = bytearray(55)
    data[:4] = b"DMRD"
    data[4] = sequence
    data[5:8] = source.to_bytes(3, "big")
    data[8:11] = tg.to_bytes(3, "big")
    data[15] = flags | (0x80 if slot == 2 else 0)
    data[16:20] = stream
    data[20:53] = bytes(range(33))
    return bytes(data)


def test_decodes_both_slots_and_preserves_voice_payload():
    first = parse_packet(frame())
    second = parse_packet(frame(slot=2, tg=46001))
    assert (first.slot, first.source_id, first.tg, first.kind) == (1, 2353660, 91, "voice")
    assert (second.slot, second.tg) == (2, 46001)
    assert first.burst == bytes(range(33))
    assert first.stream_id == "61626364"


@pytest.mark.parametrize("data", [b"", b"DMRD", b"RPTL" + bytes(51), bytes(54), bytes(56)])
def test_rejects_non_dmrd_and_wrong_sizes(data):
    assert parse_packet(data) is None


def test_header_terminator_and_other_data_are_distinct():
    assert parse_packet(frame(flags=0x21)).kind == "header"
    assert parse_packet(frame(flags=0x22)).kind == "end"
    assert parse_packet(frame(flags=0x23)).kind == "data"


def test_private_call_is_identified():
    assert parse_packet(frame(flags=0x50)).private
