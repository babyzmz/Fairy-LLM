from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SpeechQueueItem:
    request_id: str
    text: str
    priority: int = 20


@dataclass(slots=True)
class SpeechQueue:
    items: list[SpeechQueueItem] = field(default_factory=list)

    def enqueue(self, item: SpeechQueueItem) -> None:
        self.items.append(item)
        self.items.sort(key=lambda entry: (-entry.priority, entry.request_id))

    def pop_next(self) -> SpeechQueueItem | None:
        if not self.items:
            return None
        return self.items.pop(0)

    def clear(self) -> None:
        self.items.clear()
