from dataclasses import dataclass, field
from uuid import uuid4

from backend.packets import Packet


@dataclass
class Segment:
    slot: int
    source_id: int
    tg: int
    stream_id: str
    direction: str
    started_at: float
    last_at: float
    id: str = field(default_factory=lambda: uuid4().hex)
    pcm: bytes = b""
    ended_at: float | None = None

    @property
    def duration(self) -> float:
        return len(self.pcm) / 16000


class SessionTracker:
    def __init__(self, max_seconds: float = 20, idle_seconds: float = 2):
        self.max_seconds = max_seconds
        self.idle_seconds = idle_seconds
        self.active: dict[tuple[int, str], Segment] = {}
        self.recent: dict[tuple[int, str], tuple[tuple, list[int]]] = {}

    def is_duplicate(self, packet: Packet, direction: str = "incoming") -> bool:
        stream = (packet.stream_id, packet.source_id, packet.tg)
        previous = self.recent.get((packet.slot, direction))
        return bool(packet.kind == "voice" and previous and previous[0] == stream and packet.sequence in previous[1])

    def feed(self, packet: Packet, pcm: bytes, at: float, direction: str = "incoming") -> list[Segment]:
        if packet.kind == "data":
            return []
        key = (packet.slot, direction)
        current = self.active.get(key)
        same = current and (current.stream_id, current.source_id, current.tg) == (
            packet.stream_id, packet.source_id, packet.tg
        )
        finished = []
        context = b""
        if packet.kind == "end":
            if same:
                finished.append(self._finish(key, at))
            return finished
        if self.is_duplicate(packet, direction):
            return finished
        if current and not same:
            finished.append(self._finish(key, at))
            current = None
        if current and current.duration >= self.max_seconds and packet.kind == "voice":
            # Retain one second of context so a valid long call's final words
            # survive the minimum-duration filter and keep the slot active.
            if current.duration >= 1:
                context = current.pcm[-16000:]
            finished.append(self._finish(key, at))
            current = None
        if current is None:
            current = Segment(packet.slot, packet.source_id, packet.tg, packet.stream_id, direction, at - len(context) / 16000, at, pcm=context)
            self.active[key] = current
        current.last_at = at
        if packet.kind == "voice":
            current.pcm += pcm
            stream = (packet.stream_id, packet.source_id, packet.tg)
            previous = self.recent.get(key)
            seen = previous[1] if previous and previous[0] == stream else []
            self.recent[key] = (stream, [*seen[-63:], packet.sequence])
        return finished

    def _finish(self, key: tuple[int, str], at: float) -> Segment:
        segment = self.active.pop(key)
        segment.ended_at = at
        return segment

    def flush_expired(self, at: float) -> list[Segment]:
        expired = [key for key, value in self.active.items() if at - value.last_at > self.idle_seconds]
        return [self._finish(key, self.active[key].last_at) for key in expired]

    def finish_all(self, at: float) -> list[Segment]:
        return [self._finish(key, at) for key in list(self.active)]
