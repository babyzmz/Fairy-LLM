from __future__ import annotations

from uuid import UUID

from fairy_core.assistant.models import AssistantTurn, AssistantTurnStatus
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import ProviderCancelledError


class AssistantTurnReader:
    def __init__(self, unit_of_work_factory: CoreUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def get(self, turn_id: UUID) -> AssistantTurn:
        with self._unit_of_work_factory() as unit_of_work:
            return require_turn(unit_of_work, turn_id)

    def require_active(self, turn_id: UUID) -> None:
        turn = self.get(turn_id)
        if turn.status is not AssistantTurnStatus.RUNNING or self.submission_cancelled(turn):
            raise ProviderCancelledError("Assistant Turn is no longer running")

    def require_waiting_for_tool(self, turn_id: UUID) -> None:
        turn = self.get(turn_id)
        if (
            turn.status is not AssistantTurnStatus.WAITING_FOR_TOOL
            or self.submission_cancelled(turn)
        ):
            raise ProviderCancelledError("Assistant Turn is no longer waiting for a tool")

    def submission_cancelled(self, turn: AssistantTurn) -> bool:
        prefix = "message-turn:"
        if not turn.idempotency_key.startswith(prefix):
            return False
        key = turn.idempotency_key[len(prefix):]
        if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            return False
        with self._unit_of_work_factory() as unit:
            return unit.assistant.message_cancellation_requested(key, turn.conversation_id)

    def tool_count(self, turn_id: UUID) -> int:
        with self._unit_of_work_factory() as unit_of_work:
            return len(unit_of_work.assistant.list_tool_invocations(turn_id))

    def release_terminal_images(self, turn_id: UUID, image_attachments) -> None:
        try:
            turn = self.get(turn_id)
        except KeyError:
            return
        if turn.status in {
            AssistantTurnStatus.COMPLETED,
            AssistantTurnStatus.CANCELLED,
            AssistantTurnStatus.FAILED,
        }:
            image_attachments.release(turn_id)


def require_turn(unit_of_work, turn_id: UUID) -> AssistantTurn:
    turn = unit_of_work.assistant.get_turn(turn_id)
    if turn is None:
        raise KeyError(f"Assistant Turn not found: {turn_id}")
    return turn


def require_task(unit_of_work, task_id: UUID):
    task = unit_of_work.state.get_task(task_id)
    if task is None:
        raise KeyError(f"task not found: {task_id}")
    return task


__all__ = ["AssistantTurnReader", "require_task", "require_turn"]
