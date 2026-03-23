from __future__ import annotations

from dataclasses import asdict, dataclass, field
from time import time
from typing import Any


@dataclass(slots=True)
class SystemEvent:
    event: str
    timestamp_ms: int = field(default_factory=lambda: int(time() * 1000))
    request_id: str | None = None
    session_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
