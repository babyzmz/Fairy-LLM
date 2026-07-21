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
from fairy_core.assistant.trace_models import TraceStepKind, TraceStepStatus, TurnTrace
from fairy_core.assistant.trace_runtime import TurnTraceRuntime
from fairy_core.assistant.work_queue import AssistantTurnWorkClaim
from fairy_core.commanding.models import CommandRun, CommandStatus, EventVisibility
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.commanding.settings import ExecutionPolicyResolver
from fairy_core.domain.errors import (
    IdempotencyConflictError,
    InvalidTransitionError,
    VersionConflictError,
)
from fairy_core.domain.models import ScopeContract, Task
from fairy_core.knowledge.harness import HarnessManifestBuilder, KnowledgeSnapshotBuilder
from fairy_core.model_catalog.models import ModelSelectionSnapshot
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
        registry: ToolRegistry | None = None,
        execution_policy: ExecutionPolicyResolver | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._scope_resolver = scope_resolver
        self._knowledge_snapshots = KnowledgeSnapshotBuilder()
        self._harness_manifests = HarnessManifestBuilder(
            registry or ToolRegistry(),
            execution_policy,
        )
        self._trace = TurnTraceRuntime(unit_of_work_factory)

    def create_turn(
        self,
        *,
        task_id: UUID,
        profile_id: str,
        idempotency_key: str,
        model_selection: ModelSelectionSnapshot | None = None,
    ) -> AssistantTurn:
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            if model_selection is not None:
                current_selection = unit_of_work.model_catalog.get_selection()
                if (
                    current_selection.mode is not model_selection.mode
                    or current_selection.model_id != model_selection.model_id
                    or current_selection.allow_free_fallback != model_selection.allow_free_fallback
                    or current_selection.zero_data_retention != model_selection.zero_data_retention
                    or current_selection.revision != model_selection.revision
                ):
                    raise VersionConflictError("model selection changed before Turn creation")
            existing = unit_of_work.assistant.find_turn_by_idempotency_key(idempotency_key.strip())
            if existing is not None:
                scope = self._scope_resolver(unit_of_work.state, task)
                self._validate_replay(
                    existing,
                    task=task,
                    scope=scope,
                    profile_id=profile_id,
                    model_selection=model_selection,
                )
                if self._ensure_trace(unit_of_work, existing, legacy=True):
                    unit_of_work.commit()
                return existing
            scope = self._bind_harness_context(
                unit_of_work,
                task=task,
                profile_id=profile_id,
                model_selection=model_selection,
            )
            turn = AssistantTurn.create(
                task=task,
                scope=scope,
                profile_id=profile_id,
                idempotency_key=idempotency_key,
                model_selection=model_selection,
            )
            persisted, inserted = unit_of_work.assistant.create_turn_if_absent(turn)
            if not inserted:
                self._validate_replay(
                    persisted,
                    task=task,
                    scope=scope,
                    profile_id=profile_id,
                    model_selection=model_selection,
                )
                if self._ensure_trace(unit_of_work, persisted, legacy=True):
                    unit_of_work.commit()
                return persisted
            self._ensure_trace(unit_of_work, turn, legacy=False)
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

    def _bind_harness_context(
        self,
        unit_of_work,
        *,
        task: Task,
        profile_id: str,
        model_selection: ModelSelectionSnapshot | None,
    ) -> ScopeContract:
        if task.knowledge_snapshot_id is None:
            snapshot = self._knowledge_snapshots.build(unit_of_work, task=task)
            task.bind_knowledge_snapshot(snapshot.id, snapshot.content_hash)
            unit_of_work.state.save_task(task)
            unit_of_work.commands.append_domain_event(
                event_type="knowledge.snapshot.created",
                visibility=EventVisibility.DEVELOPER,
                message="Knowledge snapshot created",
                payload={
                    "snapshot_id": str(snapshot.id),
                    "snapshot_hash": snapshot.content_hash,
                    "item_count": len(snapshot.items),
                    "source_cursor": snapshot.source_cursor,
                    "status": snapshot.status.value,
                },
                actor="core",
                project_id=task.project_id,
                conversation_id=task.conversation_id,
                task_id=task.id,
            )
        else:
            snapshot = unit_of_work.knowledge.get_snapshot(
                task.knowledge_snapshot_id,
                task_id=task.id,
            )
            if snapshot is None or snapshot.content_hash != task.knowledge_snapshot_hash:
                raise ValueError("Task-bound Knowledge Snapshot is unavailable")
        scope = self._scope_resolver(unit_of_work.state, task)
        if task.harness_manifest_id is None:
            manifest = self._harness_manifests.build(
                unit_of_work,
                task=task,
                scope=scope,
                knowledge_snapshot=snapshot,
                profile_id=profile_id,
                model_selection=model_selection,
            )
            task.bind_harness_manifest(manifest.id, manifest.content_hash)
            unit_of_work.state.save_task(task)
            unit_of_work.commands.append_domain_event(
                event_type="harness.manifest.created",
                visibility=EventVisibility.DEVELOPER,
                message="Harness context manifest created",
                payload={
                    "manifest_id": str(manifest.id),
                    "manifest_hash": manifest.content_hash,
                    "knowledge_snapshot_id": str(manifest.knowledge_snapshot_id),
                    "memory_snapshot_id": str(manifest.memory_snapshot_id),
                    "tool_registry_generation": manifest.tool_registry_generation,
                },
                actor="core",
                project_id=task.project_id,
                conversation_id=task.conversation_id,
                task_id=task.id,
            )
        else:
            manifest = unit_of_work.knowledge.get_manifest(
                task.harness_manifest_id,
                task_id=task.id,
            )
            if manifest is None or manifest.content_hash != task.harness_manifest_hash:
                raise ValueError("Task-bound Harness Manifest is unavailable")
        return self._scope_resolver(unit_of_work.state, task)

    def get_turn(self, turn_id: UUID) -> AssistantTurn:
        with self._unit_of_work_factory() as unit_of_work:
            turn = unit_of_work.assistant.get_turn(turn_id)
        if turn is None:
            raise KeyError(f"Assistant Turn not found: {turn_id}")
        return turn

    def enqueue_turn_work(self, turn_id: UUID, *, force: bool = False) -> int:
        with self._unit_of_work_factory() as unit_of_work:
            revision = unit_of_work.assistant.enqueue_turn_work(turn_id, force=force)
            unit_of_work.commit()
        return revision

    def claim_turn_work(
        self,
        turn_id: UUID,
        *,
        worker_id: str,
        lease_until: datetime,
    ) -> AssistantTurnWorkClaim | None:
        with self._unit_of_work_factory() as unit_of_work:
            claim = unit_of_work.assistant.claim_turn_work(
                turn_id,
                worker_id=worker_id,
                lease_until=lease_until,
            )
            unit_of_work.commit()
        return claim

    def claim_next_turn_work(
        self,
        *,
        worker_id: str,
        lease_until: datetime,
    ) -> AssistantTurnWorkClaim | None:
        with self._unit_of_work_factory() as unit_of_work:
            claim = unit_of_work.assistant.claim_next_turn_work(
                worker_id=worker_id,
                lease_until=lease_until,
            )
            unit_of_work.commit()
        return claim

    def renew_turn_work(
        self,
        claim: AssistantTurnWorkClaim,
        *,
        lease_until: datetime,
    ) -> bool:
        with self._unit_of_work_factory() as unit_of_work:
            renewed = unit_of_work.assistant.renew_turn_work(
                claim,
                lease_until=lease_until,
            )
            unit_of_work.commit()
        return renewed

    def renew_turn_command_leases(
        self,
        turn_id: UUID,
        *,
        lease_until: datetime,
    ) -> bool:
        with self._unit_of_work_factory() as unit_of_work:
            turn = unit_of_work.assistant.get_turn(turn_id)
            if turn is None:
                return False
            run_ids = {
                step.command_run_id
                for step in unit_of_work.assistant.list_trace_steps(turn_id)
                if step.command_run_id is not None
            }
            run_ids.update(
                invocation.command_run_id
                for invocation in unit_of_work.assistant.list_tool_invocations(turn_id)
                if invocation.command_run_id is not None
            )
            if turn.budget_approval_run_id is not None:
                run_ids.add(turn.budget_approval_run_id)
            renewed_any = False
            for run_id in run_ids:
                run = unit_of_work.commands.get_run(run_id)
                if run is None or run.status is not CommandStatus.RUNNING:
                    continue
                if run.lease_owner is None:
                    return False
                if unit_of_work.commands.renew(
                    run.id,
                    lease_owner=run.lease_owner,
                    lease_fence=run.lease_fence,
                    lease_until=lease_until,
                ):
                    renewed_any = True
                    continue
                current = unit_of_work.commands.get_run(run.id)
                if current is not None and current.status is CommandStatus.RUNNING:
                    return False
            if renewed_any:
                unit_of_work.commit()
        return True

    def abandon_turn_work(self, claim: AssistantTurnWorkClaim) -> bool:
        with self._unit_of_work_factory() as unit_of_work:
            abandoned = unit_of_work.assistant.abandon_turn_work(claim)
            unit_of_work.commit()
        return abandoned

    def release_turn_work(
        self,
        claim: AssistantTurnWorkClaim,
        *,
        error_code: str | None = None,
    ) -> bool:
        with self._unit_of_work_factory() as unit_of_work:
            released = unit_of_work.assistant.release_turn_work(
                claim,
                error_code=error_code,
            )
            unit_of_work.commit()
        return released

    def cancel_turn_work(self, turn_id: UUID) -> bool:
        with self._unit_of_work_factory() as unit_of_work:
            cancelled = unit_of_work.assistant.cancel_turn_work(turn_id)
            unit_of_work.commit()
        return cancelled

    def pending_turn_work_ids(self) -> tuple[UUID, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.assistant.pending_turn_work_ids()

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
            original_user_message = unit_of_work.assistant.message_for_turn(
                original.id,
                MessageRole.USER,
            )
            scope = self._scope_resolver(unit_of_work.state, task)
            existing = unit_of_work.assistant.find_turn_by_idempotency_key(normalized_key)
            if existing is not None:
                self._validate_replay(
                    existing,
                    task=task,
                    scope=scope,
                    profile_id=original.profile_id,
                    model_selection=original.model_selection,
                )
                if self._ensure_trace(unit_of_work, existing, legacy=True):
                    unit_of_work.commit()
                return existing
            retry = AssistantTurn.create(
                task=task,
                scope=scope,
                profile_id=original.profile_id,
                idempotency_key=normalized_key,
                model_selection=original.model_selection,
            )
            persisted, inserted = unit_of_work.assistant.create_turn_if_absent(retry)
            if not inserted:
                self._validate_replay(
                    persisted,
                    task=task,
                    scope=scope,
                    profile_id=original.profile_id,
                    model_selection=original.model_selection,
                )
                if self._ensure_trace(unit_of_work, persisted, legacy=True):
                    unit_of_work.commit()
                return persisted
            self._ensure_trace(unit_of_work, retry, legacy=False)
            retry_message = Message.create(
                conversation_id=task.conversation_id,
                task_id=task.id,
                turn_id=retry.id,
                sequence=unit_of_work.assistant.next_message_sequence(task.conversation_id),
                role=MessageRole.USER,
                visibility=MessageVisibility.INTERNAL,
                content=(
                    original_user_message.content
                    if original_user_message is not None
                    else task.user_request
                ),
            )
            unit_of_work.assistant.append_message(retry_message)
            unit_of_work.commit()
        return retry

    @staticmethod
    def _ensure_trace(unit_of_work, turn: AssistantTurn, *, legacy: bool) -> bool:
        _trace, inserted = unit_of_work.assistant.create_trace_if_absent(
            TurnTrace.create(
                turn_id=turn.id,
                conversation_id=turn.conversation_id,
                task_id=turn.task_id,
                legacy=legacy,
            )
        )
        return inserted

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
                allowed_visibilities=frozenset({MessageVisibility.USER}),
            )

    def recover_orphaned_turns(
        self,
        *,
        live_turn_ids: tuple[UUID, ...] | None = None,
    ) -> tuple[AssistantTurn, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            effective_live_turn_ids = (
                tuple(
                    sorted(
                        {
                            *unit_of_work.assistant.live_turn_ids(),
                            *unit_of_work.assistant.pending_turn_work_ids(),
                        },
                        key=str,
                    )
                )
                if live_turn_ids is None
                else live_turn_ids
            )
            interrupted = unit_of_work.assistant.interrupt_orphaned_turns(
                live_turn_ids=effective_live_turn_ids
            )
            for turn in interrupted:
                self._ensure_trace(unit_of_work, turn, legacy=True)
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
                    self._interrupt_command(unit_of_work, turn.id, run)
                self._trace.finish_active_steps_in_unit(
                    unit_of_work,
                    turn_id=turn.id,
                    run=None,
                    status=TraceStepStatus.FAILED,
                    public_detail="Worker interrupted before the step completed.",
                    emit_events=False,
                )
                self._trace.complete_trace_in_unit(unit_of_work, turn_id=turn.id)
            if interrupted:
                unit_of_work.commit()
        return interrupted

    def resumable_waiting_turn_ids(self) -> tuple[UUID, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.assistant.resumable_waiting_turn_ids()

    def _interrupt_command(self, unit_of_work, turn_id: UUID, run: CommandRun) -> None:
        if run.status not in {CommandStatus.QUEUED, CommandStatus.RUNNING}:
            return
        commands = unit_of_work.commands
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
        for kind in (
            TraceStepKind.MODEL,
            TraceStepKind.APPROVAL,
            TraceStepKind.TOOL,
        ):
            self._trace.transition_command_step_in_unit(
                unit_of_work,
                run=run,
                kind=kind,
                status=TraceStepStatus.FAILED,
                public_detail="Worker interrupted before the step completed.",
            )
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
        model_selection: ModelSelectionSnapshot | None,
    ) -> None:
        if (
            existing.task_id != task.id
            or existing.conversation_id != task.conversation_id
            or existing.profile_id != profile_id.strip()
            or existing.scope_digest != scope.scope_digest
            or existing.memory_snapshot_id != task.memory_snapshot_id
            or existing.memory_snapshot_hash != task.memory_snapshot_hash
            or existing.knowledge_snapshot_id != task.knowledge_snapshot_id
            or existing.knowledge_snapshot_hash != task.knowledge_snapshot_hash
            or existing.harness_manifest_id != task.harness_manifest_id
            or existing.harness_manifest_hash != task.harness_manifest_hash
            or existing.model_selection != model_selection
        ):
            raise IdempotencyConflictError(
                "turn idempotency key was already used for a different request"
            )


__all__ = ["AssistantLedgerApplication", "ScopeResolver"]
