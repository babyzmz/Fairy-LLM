from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class InterruptionDecision:
    interrupt_speech: bool
    cancel_previous_render: bool
    reason: str
    previous_request_id: str = ""


class ConcurrentTaskController:
    def __init__(self) -> None:
        self.active_request_id: str = ""

    def begin_request(self, request_id: str) -> InterruptionDecision:
        previous = self.active_request_id
        self.active_request_id = request_id
        if previous and previous != request_id:
            return InterruptionDecision(
                interrupt_speech=True,
                cancel_previous_render=True,
                reason="new_request_supersedes_previous",
                previous_request_id=previous,
            )
        return InterruptionDecision(
            interrupt_speech=False,
            cancel_previous_render=False,
            reason="no_active_conflict",
            previous_request_id="",
        )

    def finish_request(self, request_id: str) -> None:
        if self.active_request_id == request_id:
            self.active_request_id = ""
