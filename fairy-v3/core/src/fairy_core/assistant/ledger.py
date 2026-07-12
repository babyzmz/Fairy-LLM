from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fairy_core.assistant.models import (
    AssistantTurn,
    AssistantTurnStatus,
    ImportedMessage,
    Message,
    MessageRole,
    MessageVisibility,
    ToolInvocationStatus,
)
from fairy_core.commanding.models import CommandRun, CommandStatus, EventVisibility
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

    def retry_turn(
        self,
        *,
        turn_id: UUID,
        idempotency_key: str,
    ) -> AssistantTurn:
        normalized_key = idempotency_key.strip()
        with self._unit_of_work_factory() as unit_of_work:
            original = unit_of_work.assistant.get_turn(turn_id)
            if original is None:
                raise KeyError(f"Assistant Turn not found: {turn_id}")
            if original.status not in {
                AssistantTurnStatus.COMPLETED,
                AssistantTurnStatus.CANCELLED,
                AssistantTurnStatus.FAILED,
            }:
                raise InvalidTransitionError("only a terminal Assistant Turn can be retried")
            task = unit_of_work.state.get_task(original.task_id)
            if task is None:
                raise KeyError(f"task not found: {original.task_id}")
            scope = self._scope_resolver(unit_of_work.state, task)
            existing = unit_of_work.assistant.find_turn_by_idempotency_key(normalized_key)
            if existing is not None:
                self._validate_replay(
                    existing,
                    task=task,
                    scope=scope,
                    profile_id=original.profile_id,
                )
                return existing
            retry = AssistantTurn.create(
                task=task,
                scope=scope,
                profile_id=original.profile_id,
                idempotency_key=normalized_key,
            )
            persisted, inserted = unit_of_work.assistant.create_turn_if_absent(retry)
            if not inserted:
                self._validate_replay(
                    persisted,
                    task=task,
                    scope=scope,
                    profile_id=original.profile_id,
                )
                return persisted
            unit_of_work.commit()
        return retry

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
    ) -> StatePage[Message | ImportedMessage]:
        with self._unit_of_work_factory() as unit_of_work:
            if unit_of_work.state.get_conversation(conversation_id) is None:
                raise KeyError(f"conversation not found: {conversation_id}")
            return unit_of_work.assistant.list_transcript(
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
        live_turn_ids: tuple[UUID, ...] | None = None,
    ) -> tuple[AssistantTurn, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            effective_live_turn_ids = (
                unit_of_work.assistant.live_turn_ids() if live_turn_ids is None else live_turn_ids
            )
            interrupted = unit_of_work.assistant.interrupt_orphaned_turns(
                live_turn_ids=effective_live_turn_ids
            )
            for turn in interrupted:
                runs: dict[UUID, CommandRun] = {}
                model_run = unit_of_work.commands.active_run_for_task(
                    turn.task_id,
                    "model.generate",
                )
                if model_run is not None and model_run.input_payload.get("turn_id") == str(turn.id):
                    runs[model_run.id] = model_run
                for invocation in unit_of_work.assistant.list_tool_invocations(turn.id):
                    if invocation.status in {
                        ToolInvocationStatus.CREATED,
                        ToolInvocationStatus.QUEUED,
                        ToolInvocationStatus.RUNNING,
                    }:
                        expected_status = invocation.status
                        invocation.fail(error_code="WORKER_INTERRUPTED")
                        unit_of_work.assistant.update_tool_invocation(
                            invocation,
                            expected_status=expected_status,
                        )
                    if invocation.command_run_id is not None:
                        command = unit_of_work.commands.get_run(invocation.command_run_id)
                        if command is not None:
                            runs[command.id] = command
                for run in runs.values():
                    self._interrupt_command(unit_of_work.commands, turn.id, run)
            if interrupted:
                unit_of_work.commit()
        return interrupted

    @staticmethod
    def _interrupt_command(commands, turn_id: UUID, run: CommandRun) -> None:
        if run.status not in {CommandStatus.QUEUED, CommandStatus.RUNNING}:
            return
        lease_owner: str | None = None
        lease_fence: int | None = None
        if run.status is CommandStatus.RUNNING:
            run = commands.claim(
                run.id,
                worker_id=f"assistant-recovery:{turn_id}",
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
            )
            lease_owner = run.lease_owner
            lease_fence = run.lease_fence
        commands.append_event(
            run_id=run.id,
            event_type="assistant.turn.failed",
            visibility=EventVisibility.USER,
            message="Assistant turn interrupted",
            payload={"turn_id": str(turn_id), "error_code": "WORKER_INTERRUPTED"},
            lease_owner=lease_owner,
            lease_fence=lease_fence,
        )
        commands.transition(
            run.id,
            CommandStatus.INTERRUPTED,
            lease_owner=lease_owner,
            lease_fence=lease_fence,
        )

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
