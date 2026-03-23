from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


AssistantState = Literal["idle", "planning", "searching", "streaming_text", "rendering_cards", "speaking", "interrupted", "error"]


@dataclass(slots=True)
class AssistantStateMachine:
    state: AssistantState = "idle"
    history: list[AssistantState] = field(default_factory=lambda: ["idle"])

    def transition(self, next_state: AssistantState) -> AssistantState:
        self.state = next_state
        self.history.append(next_state)
        return self.state
