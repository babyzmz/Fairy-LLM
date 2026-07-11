from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fairy_core.assistant.models import (
    AssistantTurn,
    Message,
    MessageRole,
    MessageVisibility,
)
from fairy_core.domain.errors import IdempotencyConflictError, InvalidTransitionError
from fairy_core.domain.models import ScopeContract, Task
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.storage.pagination import StatePage
from fairy_core.storage.ports import StateStore

ScopeResolver = Callable[[StateStore, Task], ScopeContract]


class AssistantLedgerApplication:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        scope_resolver: ScopeResolver,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._scope_resolver = scope_resolver

    def create_turn(
        self,
        *,
        task_id: UUID,
        profile_id: str,
        idempotency_key: str,
    ) -> AssistantTurn:
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            scope = self._scope_resolver(unit_of_work.state, task)
            existing = unit_of_work.assistant.find_turn_by_idempotency_key(idempotency_key.strip())
            if existing is not None:
                self._validate_replay(
                    existing,
                    task=task,
                    scope=scope,
                    profile_id=profile_id,
                )
                return existing
            turn = AssistantTurn.create(
                task=task,
                scope=scope,
                profile_id=profile_id,
                idempotency_key=idempotency_key,
            )
            persisted, inserted = unit_of_work.assistant.create_turn_if_absent(turn)
            if not inserted:
                self._validate_replay(
                    persisted,
                    task=task,
                    scope=scope,
                    profile_id=profile_id,
                )
                return persisted
            message = Message.create(
                conversation_id=task.conversation_id,
                task_id=task.id,
                turn_id=turn.id,
                sequence=unit_of_work.assistant.next_message_sequence(task.conversation_id),
                role=MessageRole.USER,
                visibility=MessageVisibility.USER,
                content=task.user_request,
            )
            unit_of_work.assistant.append_message(message)
            unit_of_work.commit()
        return turn

    def get_turn(self, turn_id: UUID) -> AssistantTurn:
        with self._unit_of_work_factory() as unit_of_work:
            turn = unit_of_work.assistant.get_turn(turn_id)
        if turn is None:
            raise KeyError(f"Assistant Turn not found: {turn_id}")
        return turn

    def cancel_turn(
        self,
        *,
        turn_id: UUID,
        expected_cancellation_revision: int,
    ) -> AssistantTurn:
        with self._unit_of_work_factory() as unit_of_work:
            turn = unit_of_work.assistant.get_turn(turn_id)
            if turn is None:
                raise KeyError(f"Assistant Turn not found: {turn_id}")
            if turn.cancellation_revision != expected_cancellation_revision:
                raise InvalidTransitionError(
                    "Assistant Turn cancellation revision changed concurrently"
                )
            expected_status = turn.status
            turn.cancel()
            unit_of_work.assistant.update_turn(
                turn,
                expected_status=expected_status,
                expected_cancellation_revision=expected_cancellation_revision,
            )
            unit_of_work.commit()
        return turn

    def list_messages(
        self,
        *,
        conversation_id: UUID,
        limit: int,
        cursor: str | None,
    ) -> StatePage[Message]:
        with self._unit_of_work_factory() as unit_of_work:
            if unit_of_work.state.get_conversation(conversation_id) is None:
                raise KeyError(f"conversation not found: {conversation_id}")
            return unit_of_work.assistant.list_messages(
                conversation_id=conversation_id,
                limit=limit,
                cursor=cursor,
                allowed_visibilities=frozenset(
                    {MessageVisibility.USER, MessageVisibility.DEVELOPER}
                ),
            )

    def recover_orphaned_turns(
        self,
        *,
        live_turn_ids: tuple[UUID, ...] = (),
    ) -> tuple[AssistantTurn, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            interrupted = unit_of_work.assistant.interrupt_orphaned_turns(
                live_turn_ids=live_turn_ids
            )
            if interrupted:
                unit_of_work.commit()
        return interrupted

    @staticmethod
    def _validate_replay(
        existing: AssistantTurn,
        *,
        task: Task,
        scope: ScopeContract,
        profile_id: str,
    ) -> None:
        if (
            existing.task_id != task.id
            or existing.conversation_id != task.conversation_id
            or existing.profile_id != profile_id.strip()
            or existing.scope_digest != scope.scope_digest
            or existing.memory_snapshot_id != task.memory_snapshot_id
            or existing.memory_snapshot_hash != task.memory_snapshot_hash
        ):
            raise IdempotencyConflictError(
                "turn idempotency key was already used for a different request"
            )


__all__ = ["AssistantLedgerApplication", "ScopeResolver"]
