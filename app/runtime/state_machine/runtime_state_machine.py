from __future__ import annotations

from dataclasses import asdict, dataclass, field
from time import time
from typing import Literal


RuntimeAssistantState = Literal[
    "booting",
    "warming_up",
    "idle",
    "thinking",
    "analyzing",
    "replying",
    "error",
    "sleeping",
]

_ALLOWED_TRANSITIONS: dict[RuntimeAssistantState, set[RuntimeAssistantState]] = {
    "booting": {"warming_up", "idle", "error", "sleeping"},
    "warming_up": {"idle", "thinking", "analyzing", "error", "sleeping"},
    "idle": {"booting", "warming_up", "thinking", "error", "sleeping"},
    "thinking": {"analyzing", "replying", "idle", "error", "sleeping"},
    "analyzing": {"replying", "thinking", "idle", "error", "sleeping"},
    "replying": {"replying", "analyzing", "idle", "error", "sleeping"},
    "error": {"booting", "warming_up", "idle", "thinking", "analyzing", "replying", "sleeping"},
    "sleeping": {"booting", "warming_up", "idle", "error"},
}


@dataclass(slots=True)
class RuntimeStateTransition:
    previous_state: RuntimeAssistantState
    current_state: RuntimeAssistantState
    reason: str = ""
    request_id: str | None = None
    session_id: str | None = None
    timestamp_ms: int = field(default_factory=lambda: int(time() * 1000))

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class RuntimeStateMachine:
    def __init__(self, *, initial_state: RuntimeAssistantState = "booting", max_history: int = 80) -> None:
        self.current_state: RuntimeAssistantState = initial_state
        self._max_history = max(max_history, 1)
        self._trace: list[RuntimeStateTransition] = []

    def transition(
        self,
        next_state: RuntimeAssistantState,
        *,
        reason: str = "",
        request_id: str | None = None,
        session_id: str | None = None,
        force: bool = False,
    ) -> RuntimeStateTransition | None:
        if next_state == self.current_state:
            return None
        if not force and next_state not in _ALLOWED_TRANSITIONS.get(self.current_state, set()):
            return None
        transition = RuntimeStateTransition(
            previous_state=self.current_state,
            current_state=next_state,
            reason=str(reason or "").strip(),
            request_id=request_id or None,
            session_id=session_id or None,
        )
        self.current_state = next_state
        self._trace.append(transition)
        if len(self._trace) > self._max_history:
            self._trace = self._trace[-self._max_history :]
        return transition

    def trace_snapshot(self) -> list[dict[str, object]]:
        return [item.to_dict() for item in self._trace]
