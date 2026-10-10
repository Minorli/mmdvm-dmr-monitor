from dataclasses import dataclass


@dataclass(frozen=True)
class Packet:
    slot: int
    source_id: int
    tg: int
    stream_id: str
    sequence: int
    kind: str
    private: bool
    burst: bytes


def parse_packet(data: bytes) -> Packet | None:
    if len(data) != 55 or data[:4] != b"DMRD":
        return None
    flags = data[15]
    kind = "voice"
    if flags & 0x20:
        kind = {1: "header", 2: "end"}.get(flags & 15, "data")
    return Packet(
        slot=2 if flags & 0x80 else 1,
        source_id=int.from_bytes(data[5:8], "big"),
        tg=int.from_bytes(data[8:11], "big"),
        stream_id=data[16:20].hex(),
        sequence=data[4],
        kind=kind,
        private=bool(flags & 0x40),
        burst=data[20:53],
    )
