from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ResponseStage = Literal["idle", "planning", "executing", "streaming", "rendering", "speaking", "completed", "failed"]


@dataclass(slots=True)
class ResponseStateMachine:
    stage: ResponseStage = "idle"
    timeline: list[ResponseStage] = field(default_factory=lambda: ["idle"])

    def transition(self, next_stage: ResponseStage) -> ResponseStage:
        self.stage = next_stage
        self.timeline.append(next_stage)
        return self.stage
