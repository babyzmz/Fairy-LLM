from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from fairy_core.assistant.interpretation import (
    AssistantRequestInterpretationRevision,
    InterpretationConfidence,
    InterpretationDisposition,
    RequestAction,
    fallback_interpretation,
)
from fairy_core.assistant.models import (
    AssistantTurn,
    AssistantTurnStatus,
    ImportedMessage,
    Message,
    MessageRole,
    MessageVisibility,
)
from fairy_core.assistant.system_intent import unrouted_interpretation
from fairy_core.assistant.trace_models import TurnTrace
from fairy_core.assistant.trace_runtime import TurnTraceRuntime
from fairy_core.assistant.turn_reader import require_turn
from fairy_core.assistant.workflow_plan import (
    ASSISTANT_WORKFLOW_ENGINE_VERSION,
    apply_pending_assistant_steering,
    assistant_workflow_plan,
)
from fairy_core.commanding.models import CommandStatus, EventVisibility
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.commanding.settings import ExecutionPolicyResolver
from fairy_core.contracts.common import ExecutionTarget
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
from fairy_core.workflow.models import (
    WorkflowInstructionStatus,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowTriggerKind,
)

ScopeResolver = Callable[[StateStore, Task], ScopeContract]


class AssistantLedgerApplication:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        scope_resolver: ScopeResolver,
        registry: ToolRegistry | None = None,
        execution_policy: ExecutionPolicyResolver | None = None,
        execution_target: ExecutionTarget = ExecutionTarget.LOCAL,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._scope_resolver = scope_resolver
        self._knowledge_snapshots = KnowledgeSnapshotBuilder()
        self._harness_manifests = HarnessManifestBuilder(
            registry or ToolRegistry(),
            execution_policy,
        )
        self._trace = TurnTraceRuntime(unit_of_work_factory)
        self._execution_target = ExecutionTarget(execution_target)

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
            active = unit_of_work.assistant.nonterminal_turn_for_conversation(task.conversation_id)
            if active is not None and active.cancellation_pending:
                raise InvalidTransitionError("The previous operation is still stopping")
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
                execution_engine_version=ASSISTANT_WORKFLOW_ENGINE_VERSION,
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
            self._bind_new_workflow(unit_of_work, turn=turn, task=task)
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
        return self.get_turn(turn.id)

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

    def get_interpretation(
        self,
        *,
        turn_id: UUID,
        revision: int | None = None,
    ) -> AssistantRequestInterpretationRevision:
        with self._unit_of_work_factory() as unit_of_work:
            if unit_of_work.assistant.get_turn(turn_id) is None:
                raise KeyError(f"Assistant Turn not found: {turn_id}")
            interpretation = unit_of_work.assistant.get_interpretation(turn_id, revision)
        if interpretation is None:
            raise KeyError(f"Assistant Turn interpretation not found: {turn_id}")
        return interpretation

    def respond_to_clarification(
        self,
        *,
        turn_id: UUID,
        content: str,
        expected_interpretation_revision: int,
        idempotency_key: str,
    ) -> AssistantTurn:
        normalized_content = content.strip()
        normalized_key = idempotency_key.strip()
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            replay = unit_of_work.assistant.find_interpretation_by_idempotency_key(
                turn_id,
                normalized_key,
            )
            if replay is not None:
                source = unit_of_work.assistant.get_message(replay.source_message_id)
                if source is None or source.content != normalized_content:
                    raise IdempotencyConflictError(
                        "clarification idempotency key was reused with different content"
                    )
                return turn
            if turn.status is not AssistantTurnStatus.WAITING_FOR_INPUT:
                raise InvalidTransitionError("Assistant Turn is not waiting for user input")
            if turn.active_interpretation_revision != expected_interpretation_revision:
                raise VersionConflictError("Assistant interpretation revision changed")
            current = unit_of_work.assistant.get_interpretation(
                turn.id,
                expected_interpretation_revision,
            )
            if (
                current is None
                or current.disposition is not InterpretationDisposition.CLARIFICATION_REQUIRED
            ):
                raise InvalidTransitionError("Assistant Turn has no active clarification")
            message = Message.create(
                conversation_id=turn.conversation_id,
                task_id=turn.task_id,
                turn_id=turn.id,
                sequence=unit_of_work.assistant.next_message_sequence(turn.conversation_id),
                role=MessageRole.USER,
                visibility=MessageVisibility.USER,
                content=normalized_content,
            )
            next_revision = expected_interpretation_revision + 1
            clarification_constraint = f"Clarification response: {normalized_content[:1_950]}"
            revised = AssistantRequestInterpretationRevision.create(
                turn_id=turn.id,
                revision=next_revision,
                idempotency_key=normalized_key,
                source_message_id=message.id,
                source_message=message.content,
                normalized_goal=current.normalized_goal,
                action=current.action,
                objectives=current.objectives,
                targets=current.targets,
                constraints=tuple(dict.fromkeys((*current.constraints, clarification_constraint))),
                deliverable=current.deliverable,
                evidence_requirements=current.evidence_requirements,
                assumptions=current.assumptions,
                missing_information=current.missing_information,
                confidence=current.confidence,
                disposition=InterpretationDisposition.CLARIFICATION_REQUIRED,
                public_summary="Clarification response received",
                clarification_question=current.clarification_question,
            )
            unit_of_work.assistant.append_message(message)
            unit_of_work.assistant.append_interpretation(
                revised,
                expected_revision=expected_interpretation_revision,
            )
            expected_status = turn.status
            expected_cancellation = turn.cancellation_revision
            turn.bind_interpretation(
                next_revision,
                expected_revision=expected_interpretation_revision,
            )
            turn.reopen_routing_after_input()
            turn.resume_from_input()
            unit_of_work.assistant.update_turn(
                turn,
                expected_status=expected_status,
                expected_cancellation_revision=expected_cancellation,
            )
            unit_of_work.commands.append_domain_event(
                event_type="assistant.turn.clarification_received",
                visibility=EventVisibility.USER,
                message="Clarification received",
                payload={
                    "turn_id": str(turn.id),
                    "interpretation_revision": next_revision,
                },
                actor="user",
                conversation_id=turn.conversation_id,
                task_id=turn.task_id,
            )
            unit_of_work.commit()
        return self.get_turn(turn_id)

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
            if original.cancellation_pending:
                raise InvalidTransitionError("The previous operation is still stopping")
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
                execution_engine_version=ASSISTANT_WORKFLOW_ENGINE_VERSION,
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
            self._bind_new_workflow(unit_of_work, turn=retry, task=task)
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
        return self.get_turn(retry.id)

    def _bind_new_workflow(self, unit_of_work, *, turn: AssistantTurn, task: Task) -> None:
        run = WorkflowRun.create(
            owner_kind="assistant_turn",
            owner_id=str(turn.id),
            execution_target=self._execution_target,
            trigger_kind=WorkflowTriggerKind.USER_TURN,
            idempotency_key=f"assistant-turn:{turn.id}",
            conversation_id=turn.conversation_id,
            task_id=turn.task_id,
            project_id=task.project_id,
            engine_version=ASSISTANT_WORKFLOW_ENGINE_VERSION,
        )
        nodes, edges = assistant_workflow_plan(
            run_id=run.id,
            revision=1,
            turn_id=turn.id,
        )
        unit_of_work.workflows.create(run, nodes=nodes, edges=edges)
        unit_of_work.workflows.request_pause(run.id)
        turn.bind_workflow(run.id, engine_version=ASSISTANT_WORKFLOW_ENGINE_VERSION)
        unit_of_work.assistant.bind_turn_workflow(
            turn.id,
            workflow_run_id=run.id,
            engine_version=ASSISTANT_WORKFLOW_ENGINE_VERSION,
        )

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
            unit_of_work.commands.append_domain_event(
                event_type="assistant.turn.cancel_requested",
                visibility=EventVisibility.USER,
                message="Cancellation accepted; running operations may still be stopping",
                payload={
                    "turn_id": str(turn.id),
                    "cancellation_revision": turn.cancellation_revision,
                },
                actor="user",
                conversation_id=turn.conversation_id,
                task_id=turn.task_id,
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

    def recover_orphaned_turns(self) -> tuple[AssistantTurn, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            legacy_turn_ids = unit_of_work.assistant.legacy_nonterminal_turn_ids()
        if legacy_turn_ids:
            raise RuntimeError(
                "Non-terminal pre-Workflow Assistant Turns block this Core upgrade: "
                + ", ".join(str(value) for value in legacy_turn_ids)
            )
        return ()

    def resumable_waiting_turn_ids(self) -> tuple[UUID, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.assistant.resumable_waiting_turn_ids()

    def resumable_workflow_turn_ids(self) -> tuple[UUID, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.assistant.resumable_workflow_turn_ids()

    def steer_turn(
        self,
        *,
        turn_id: UUID,
        instruction: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> AssistantTurn:
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            if turn.workflow_run_id is None:
                raise InvalidTransitionError("Legacy Assistant Turn cannot be steered")
            snapshot = unit_of_work.workflows.get(turn.workflow_run_id)
            if snapshot is None:
                raise RuntimeError("Assistant Workflow is unavailable")
            replay = next(
                (
                    value
                    for value in snapshot.instructions
                    if value.idempotency_key == idempotency_key
                ),
                None,
            )
            if replay is not None:
                unit_of_work.workflows.add_instruction(
                    turn.workflow_run_id,
                    instruction=instruction,
                    expected_revision=expected_revision,
                    idempotency_key=idempotency_key,
                )
                unit_of_work.commit()
                return turn
            if turn.status not in {
                AssistantTurnStatus.RUNNING,
                AssistantTurnStatus.WAITING_FOR_TOOL,
            }:
                raise InvalidTransitionError("Only an active Assistant Turn can be steered")
            if replay is None and any(
                value.status is WorkflowInstructionStatus.PENDING for value in snapshot.instructions
            ):
                raise InvalidTransitionError("Assistant Workflow already has a pending update")
            if replay is None and snapshot.run.status is WorkflowRunStatus.WAITING_FOR_APPROVAL:
                raise InvalidTransitionError(
                    "Resolve the current approval before updating this task"
                )
            workflow_instruction = unit_of_work.workflows.add_instruction(
                turn.workflow_run_id,
                instruction=instruction,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
            )
            if replay is None:
                message = Message.create(
                    conversation_id=turn.conversation_id,
                    task_id=turn.task_id,
                    turn_id=turn.id,
                    sequence=unit_of_work.assistant.next_message_sequence(turn.conversation_id),
                    role=MessageRole.USER,
                    visibility=MessageVisibility.USER,
                    content=instruction,
                )
                unit_of_work.assistant.append_message(message)
                next_interpretation_revision: int | None = None
                current_interpretation = unit_of_work.assistant.get_interpretation(
                    turn.id,
                    turn.active_interpretation_revision,
                )
                if current_interpretation is None and turn.model_selection is not None:
                    original = unit_of_work.assistant.message_for_turn(turn.id, MessageRole.USER)
                    pending = replace(
                        fallback_interpretation(
                            turn_id=turn.id,
                            revision=1,
                            source_message_id=message.id,
                            source_message=message.content,
                            action=RequestAction.ANSWER,
                        ),
                        idempotency_key=f"steer:{idempotency_key}",
                        confidence=InterpretationConfidence.LOW,
                        constraints=(f"Original request: {original.content[:1_950]}",)
                        if original is not None
                        else (),
                    )
                    unit_of_work.assistant.append_interpretation(pending, expected_revision=None)
                    next_interpretation_revision = 1
                    expected_status = turn.status
                    expected_cancellation = turn.cancellation_revision
                    turn.bind_interpretation(1, expected_revision=None)
                    turn.invalidate_routing_for_steering()
                    unit_of_work.assistant.update_turn(
                        turn,
                        expected_status=expected_status,
                        expected_cancellation_revision=expected_cancellation,
                    )
                if current_interpretation is not None:
                    next_interpretation_revision = current_interpretation.revision + 1
                    update_constraint = f"Updated requirement: {instruction[:1_950]}"
                    revised_interpretation = AssistantRequestInterpretationRevision.create(
                        turn_id=turn.id,
                        revision=next_interpretation_revision,
                        idempotency_key=f"steer:{idempotency_key}",
                        source_message_id=message.id,
                        source_message=message.content,
                        normalized_goal=current_interpretation.normalized_goal,
                        action=current_interpretation.action,
                        objectives=current_interpretation.objectives,
                        targets=current_interpretation.targets,
                        constraints=tuple(
                            dict.fromkeys((*current_interpretation.constraints, update_constraint))
                        ),
                        deliverable=current_interpretation.deliverable,
                        evidence_requirements=current_interpretation.evidence_requirements,
                        assumptions=current_interpretation.assumptions,
                        missing_information=current_interpretation.missing_information,
                        confidence=(
                            InterpretationConfidence.LOW
                            if turn.model_selection is not None
                            else current_interpretation.confidence
                        ),
                        disposition=current_interpretation.disposition,
                        public_summary="The active task requirements were updated",
                        clarification_question=current_interpretation.clarification_question,
                    )
                    if turn.model_selection is None:
                        revised_interpretation = replace(
                            unrouted_interpretation(
                                turn_id=turn.id,
                                revision=next_interpretation_revision,
                                source_message_id=message.id,
                                source_message=message.content,
                            ),
                            idempotency_key=revised_interpretation.idempotency_key,
                            constraints=revised_interpretation.constraints,
                        )
                    unit_of_work.assistant.append_interpretation(
                        revised_interpretation,
                        expected_revision=current_interpretation.revision,
                    )
                    if turn.model_selection is not None:
                        expected_status = turn.status
                        expected_cancellation = turn.cancellation_revision
                        turn.bind_interpretation(
                            next_interpretation_revision,
                            expected_revision=current_interpretation.revision,
                        )
                        turn.invalidate_routing_for_steering()
                        unit_of_work.assistant.update_turn(
                            turn,
                            expected_status=expected_status,
                            expected_cancellation_revision=expected_cancellation,
                        )
                paused = unit_of_work.workflows.request_pause(turn.workflow_run_id)
                unit_of_work.commands.append_domain_event(
                    event_type="assistant.turn.steered",
                    visibility=EventVisibility.USER,
                    message="Assistant task requirements updated",
                    payload={
                        "turn_id": str(turn.id),
                        "message_id": str(message.id),
                        "workflow_run_id": str(turn.workflow_run_id),
                        "instruction_id": str(workflow_instruction.id),
                        "interpretation_revision": next_interpretation_revision,
                    },
                    actor="user",
                    project_id=snapshot.run.project_id,
                    conversation_id=turn.conversation_id,
                    task_id=turn.task_id,
                )
                if paused.run.status is WorkflowRunStatus.PAUSED:
                    apply_pending_assistant_steering(unit_of_work, turn.workflow_run_id)
            unit_of_work.commit()
        return self.get_turn(turn_id)

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
