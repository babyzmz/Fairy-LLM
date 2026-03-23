from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass(slots=True)
class ActivityEvent:
    request_id: str
    name: str
    detail: str = ""
    ts: float = field(default_factory=time)


@dataclass(slots=True)
class ActivityTracker:
    events: list[ActivityEvent] = field(default_factory=list)

    def record(self, request_id: str, name: str, detail: str = "") -> None:
        self.events.append(ActivityEvent(request_id=request_id, name=name, detail=detail))
        self.events = self.events[-200:]
