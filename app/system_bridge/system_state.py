from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.system_bridge.system_events import SystemEvent


@dataclass(slots=True)
class SystemState:
    backend_status: str = "starting"
    current_state: str = "booting"
    active_session: str | None = None
    active_stream_request: str | None = None
    is_streaming: bool = False
    last_error: str | None = None
    fairy: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    runtime_state_trace: list[dict[str, Any]] = field(default_factory=list)
    recent_events: list[SystemEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["recent_events"] = [event.to_dict() for event in self.recent_events]
        return payload
