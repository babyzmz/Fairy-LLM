from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from uuid import UUID

from fairy_core.assistant.events import append_message_created
from fairy_core.assistant.models import (
    AssistantTurn,
    AssistantTurnStatus,
    Message,
    MessageRole,
    MessageVisibility,
    ToolInvocationStatus,
)
from fairy_core.assistant.trace_models import TraceStepKind, TraceStepStatus
from fairy_core.assistant.turn_reader import require_task, require_turn
from fairy_core.commanding import CommandRun, CommandStatus, EventVisibility
from fairy_core.commanding.bus import CommandBus
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.execution import ChangesetStatus, PreviewStatus
from fairy_core.domain.models import TaskStatus, VersionVisibility, WorkspaceType
from fairy_core.execution.plans import ExecutionPlanStatus, TaskStepKind, TaskStepStatus
from fairy_core.providers import ModelExecutionRole
from fairy_core.workflow.errors import WorkflowFenceError, WorkflowRevisionError
from fairy_core.workflow.models import WorkflowAttemptClaim


class AssistantTurnLifecycleMixin:
    def _execution_completion_issue(
        self,
        turn_id: UUID,
        *,
        candidate_content: str | None = None,
        cited_evidence_receipt_ids: tuple[str, ...] | None = None,
    ) -> str | None:
        if self._execution_completion_hook is not None:
            finalization_issue = self._execution_completion_hook(turn_id)
            if finalization_issue is not None:
                return finalization_issue
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            plan = unit_of_work.state.execution_plan_for_task(turn.task_id)
            invocations = unit_of_work.assistant.list_tool_invocations(turn.id)
            evidence_issue = _evidence_completion_issue(
                turn,
                invocations,
                cited_evidence_receipt_ids,
            )
            if evidence_issue is not None:
                return evidence_issue
            if plan is None:
                if (
                    turn.routing_decision is not None
                    and turn.routing_decision.requires_workspace_changes
                ):
                    return (
                        "This Turn requires durable Workspace changes but has no Execution Plan. "
                        "Call execution.plan now, then apply every planned batch with "
                        "edit.propose_changeset before returning a final response. Do not treat "
                        "artifact.list or preview.status as completion."
                    )
                if any(
                    invocation.tool_name == "edit.propose_changeset" for invocation in invocations
                ):
                    return (
                        "A file Changeset was attempted without an Execution Plan. Create "
                        "execution.plan first, then submit exactly one complete planned batch "
                        "with edit.propose_changeset before returning a final response."
                    )
                return None
            steps = unit_of_work.state.task_steps_for_plan(plan.id)
            changesets = unit_of_work.state.changesets_for_task(turn.task_id)

        response_issue = _response_contract_issue(candidate_content, plan.manifest)
        if response_issue is not None:
            return response_issue

        implementation = [step for step in steps if step.kind is TaskStepKind.IMPLEMENT]
        if all(
            step.status in {TaskStepStatus.COMPLETED, TaskStepStatus.SKIPPED}
            for step in implementation
        ):
            return None
        if any(
            changeset.status
            in {
                ChangesetStatus.PROPOSED,
                ChangesetStatus.AWAITING_APPROVAL,
                ChangesetStatus.APPLYING,
            }
            for changeset in changesets
        ):
            return None
        first_incomplete = next(
            (
                index
                for index, step in enumerate(implementation, start=1)
                if step.status not in {TaskStepStatus.COMPLETED, TaskStepStatus.SKIPPED}
            ),
            1,
        )
        batch_files = [
            str(item["path"])
            for item in plan.manifest.get("files", [])
            if isinstance(item, dict) and item.get("batch") == first_incomplete
        ]
        visible_files = ", ".join(batch_files[:10])
        detail = (
            f"Execution Plan batch {first_incomplete} has no durable pending or applied "
            "Changeset. Call edit.propose_changeset with every file in that batch before "
            "returning a final response."
        )
        if visible_files:
            detail += f" Required files: {visible_files}."
        return detail

    def _complete_turn(
        self,
        *,
        turn_id: UUID,
        run: CommandRun,
        content: str,
        usage: dict[str, int],
        cited_evidence_receipt_ids: tuple[str, ...] = (),
        workflow_claim: WorkflowAttemptClaim | None = None,
    ) -> AssistantTurn:
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            if workflow_claim is not None:
                if (
                    workflow_claim.run_id != turn.workflow_run_id
                    or run.task_id != turn.task_id
                    or run.conversation_id != turn.conversation_id
                    or run.scope_digest != turn.scope_digest
                    or run.input_payload.get("turn_id") != str(turn.id)
                ):
                    raise WorkflowFenceError("Finalization does not belong to this Workflow Turn")
                unit_of_work.workflows.record_checkpoint(
                    workflow_claim,
                    result={
                        "turn_id": str(turn.id), "model_command_id": str(run.id),
                        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        "usage": usage,
                        "cited_evidence_receipt_ids": list(cited_evidence_receipt_ids),
                    },
                )
                if turn.status is AssistantTurnStatus.COMPLETED:
                    message = unit_of_work.assistant.message_for_turn(
                        turn.id, MessageRole.ASSISTANT,
                    )
                    if message is None or message.content != content:
                        raise WorkflowRevisionError(
                            "Completed response differs from its checkpoint"
                        )
                    unit_of_work.commit()
                    return turn
            task = require_task(unit_of_work, turn.task_id)
            completed_usage = usage
            if turn.model_selection is not None:
                attempt_usage: dict[str, int] = {}
                for attempt in unit_of_work.assistant.list_provider_attempts(turn.id):
                    for name, value in attempt.usage.items():
                        attempt_usage[name] = attempt_usage.get(name, 0) + value
                if attempt_usage:
                    completed_usage = attempt_usage
            message = Message.create(
                conversation_id=turn.conversation_id,
                task_id=turn.task_id,
                turn_id=turn.id,
                sequence=unit_of_work.assistant.next_message_sequence(turn.conversation_id),
                role=MessageRole.ASSISTANT,
                visibility=MessageVisibility.USER,
                content=content,
            )
            expected_status = turn.status
            expected_revision = turn.cancellation_revision
            if cited_evidence_receipt_ids:
                turn.cite_evidence(tuple(UUID(value) for value in cited_evidence_receipt_ids))
            turn.complete(usage=completed_usage)
            unit_of_work.assistant.append_message(message)
            unit_of_work.assistant.update_turn(
                turn,
                expected_status=expected_status,
                expected_cancellation_revision=expected_revision,
            )
            self._promote_completed_scratch_version(
                unit_of_work,
                turn=turn,
                task=task,
                run=run,
            )
            if task.project_id is None and task.status in {
                TaskStatus.EXECUTING,
                TaskStatus.PREVIEWING,
                TaskStatus.REVIEWING,
            }:
                if task.status is not TaskStatus.REVIEWING:
                    task.transition_to(TaskStatus.REVIEWING)
                task.transition_to(TaskStatus.READY)
                unit_of_work.state.save_task(task)
            self._finish_execution_plan(
                unit_of_work,
                task_id=turn.task_id,
                status=ExecutionPlanStatus.COMPLETED,
            )
            model_step = self._trace.transition_command_step_in_unit(
                unit_of_work,
                run=run,
                kind=TraceStepKind.MODEL,
                status=TraceStepStatus.SUCCEEDED,
                public_summary="Model response ready",
            )
            response_cause = model_step.id if model_step is not None else None
            if model_step is not None and model_step.model_role is ModelExecutionRole.REVIEWER:
                verification = self._trace.append_step_in_unit(
                    unit_of_work,
                    turn=turn,
                    run=run,
                    kind=TraceStepKind.VERIFICATION,
                    status=TraceStepStatus.SUCCEEDED,
                    public_summary="Response reviewed",
                    parent_step_id=model_step.id,
                    caused_by_step_id=model_step.id,
                )
                response_cause = verification.id
            self._trace.append_step_in_unit(
                unit_of_work,
                turn=turn,
                run=run,
                kind=TraceStepKind.RESPONSE,
                status=TraceStepStatus.SUCCEEDED,
                public_summary="Response ready",
                caused_by_step_id=response_cause,
            )
            self._trace.complete_trace_in_unit(unit_of_work, turn_id=turn.id)
            append_message_created(unit_of_work.commands, run=run, message=message)
            unit_of_work.commands.append_event(
                run_id=run.id,
                event_type="assistant.turn.completed",
                visibility=EventVisibility.USER,
                message="Assistant turn completed",
                payload={
                    "turn_id": str(turn.id),
                    "message_id": str(message.id),
                    "usage": completed_usage,
                },
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
            )
            self._command_bus(unit_of_work.commands).complete(
                run.id,
                output={"turn_id": str(turn.id), "message_id": str(message.id)},
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
            )
            unit_of_work.commit()
            return turn

    def _cancel_turn(
        self,
        turn_id: UUID,
        run: CommandRun | None,
    ) -> AssistantTurn:
        try:
            return self._cancel_turn_once(turn_id, run)
        except InvalidTransitionError:
            with self._unit_of_work_factory() as unit_of_work:
                persisted = require_turn(unit_of_work, turn_id)
            if persisted.status is not AssistantTurnStatus.CANCELLED:
                raise
            return self._cancel_turn_once(turn_id, run)

    def _cancel_turn_once(
        self,
        turn_id: UUID,
        run: CommandRun | None,
    ) -> AssistantTurn:
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            if turn.status is not AssistantTurnStatus.CANCELLED:
                expected_status = turn.status
                expected_revision = turn.cancellation_revision
                turn.cancel()
                unit_of_work.assistant.update_turn(
                    turn,
                    expected_status=expected_status,
                    expected_cancellation_revision=expected_revision,
                )
            self._trace.finish_active_steps_in_unit(
                unit_of_work,
                turn_id=turn.id,
                run=run,
                status=TraceStepStatus.CANCELLED,
                public_detail="Turn cancelled.",
            )
            self._trace.complete_trace_in_unit(unit_of_work, turn_id=turn.id)
            if run is not None:
                persisted_run = unit_of_work.commands.get_run(run.id)
                if persisted_run is not None and persisted_run.status in {
                    CommandStatus.QUEUED,
                    CommandStatus.WAITING_APPROVAL,
                    CommandStatus.RUNNING,
                }:
                    if persisted_run.status is CommandStatus.RUNNING:
                        unit_of_work.commands.append_event(
                            run_id=run.id,
                            event_type="assistant.turn.cancelled",
                            visibility=EventVisibility.USER,
                            message="Assistant turn cancelled",
                            payload={"turn_id": str(turn.id)},
                            lease_owner=run.lease_owner,
                            lease_fence=run.lease_fence,
                        )
                    self._command_bus(unit_of_work.commands).cancel(
                        run.id,
                        lease_owner=(
                            run.lease_owner
                            if persisted_run.status is CommandStatus.RUNNING
                            else None
                        ),
                        lease_fence=(
                            run.lease_fence
                            if persisted_run.status is CommandStatus.RUNNING
                            else None
                        ),
                    )
            task = require_task(unit_of_work, turn.task_id)
            if task.status in _ACTIVE_TASK_STATUSES:
                task.transition_to(TaskStatus.FAILED)
                unit_of_work.state.save_task(task)
            self._release_failed_scratch_draft(unit_of_work, task)
            self._finish_execution_plan(
                unit_of_work,
                task_id=turn.task_id,
                status=ExecutionPlanStatus.CANCELLED,
            )
            unit_of_work.commit()
        return turn

    def _fail_turn(
        self,
        turn_id: UUID,
        run: CommandRun | None,
        *,
        error_code: str,
    ) -> AssistantTurn:
        with self._unit_of_work_factory() as unit_of_work:
            turn = self._fail_turn_in_unit(
                unit_of_work, turn_id, run, error_code=error_code,
            )
            unit_of_work.commit()
        return turn

    def _fail_turn_in_unit(
        self, unit_of_work, turn_id: UUID, run: CommandRun | None, *, error_code: str,
    ) -> AssistantTurn:
        turn = require_turn(unit_of_work, turn_id)
        if turn.status not in {
            AssistantTurnStatus.COMPLETED,
            AssistantTurnStatus.CANCELLED,
            AssistantTurnStatus.FAILED,
        }:
            expected_status = turn.status
            expected_revision = turn.cancellation_revision
            turn.fail(error_code=error_code)
            unit_of_work.assistant.update_turn(
                turn, expected_status=expected_status,
                expected_cancellation_revision=expected_revision,
            )
        self._trace.finish_active_steps_in_unit(
            unit_of_work, turn_id=turn.id, run=run, status=TraceStepStatus.FAILED,
            public_detail=_public_failure_detail(error_code),
        )
        self._trace.complete_trace_in_unit(unit_of_work, turn_id=turn.id)
        if run is not None:
            persisted_run = unit_of_work.commands.get_run(run.id)
            if persisted_run is not None and persisted_run.status is CommandStatus.RUNNING:
                unit_of_work.commands.append_event(
                    run_id=run.id, event_type="assistant.turn.failed",
                    visibility=EventVisibility.USER, message="Assistant turn failed",
                    payload={"turn_id": str(turn.id), "error_code": error_code},
                    lease_owner=run.lease_owner, lease_fence=run.lease_fence,
                )
                self._command_bus(unit_of_work.commands).fail(
                    run.id, error_code=error_code,
                    lease_owner=run.lease_owner, lease_fence=run.lease_fence,
                )
        task = require_task(unit_of_work, turn.task_id)
        if task.status in _ACTIVE_TASK_STATUSES:
            task.transition_to(TaskStatus.FAILED)
            unit_of_work.state.save_task(task)
        self._release_failed_scratch_draft(unit_of_work, task)
        self._finish_execution_plan(
            unit_of_work, task_id=turn.task_id, status=ExecutionPlanStatus.FAILED,
        )
        return turn

    @staticmethod
    def _finish_execution_plan(unit_of_work, *, task_id: UUID, status: ExecutionPlanStatus) -> None:
        plan = unit_of_work.state.execution_plan_for_task(task_id)
        if plan is None or plan.status not in {
            ExecutionPlanStatus.ACTIVE,
            ExecutionPlanStatus.PAUSED,
        }:
            return
        expected_revision = plan.revision
        if status is ExecutionPlanStatus.COMPLETED:
            steps = unit_of_work.state.task_steps_for_plan(plan.id)
            if not steps or any(
                step.status not in {TaskStepStatus.COMPLETED, TaskStepStatus.SKIPPED}
                for step in steps
            ):
                # Project Turns finish their user-visible response before explicit Review.
                # The pending checkpoint step is completed atomically by review_task.
                return
            plan.complete()
        elif status is ExecutionPlanStatus.CANCELLED:
            plan.cancel()
        else:
            plan.fail()
        unit_of_work.state.update_execution_plan(
            plan,
            expected_revision=expected_revision,
        )

    @staticmethod
    def _promote_completed_scratch_version(unit_of_work, *, turn, task, run) -> None:
        plan = unit_of_work.state.execution_plan_for_task(task.id)
        if task.project_id is not None or plan is None:
            return
        if task.workspace_id is None or task.target_version_id is None:
            raise ValueError("completed scratch delivery has no Workspace Version")
        steps = unit_of_work.state.task_steps_for_plan(plan.id)
        if not steps or any(
            step.status not in {TaskStepStatus.COMPLETED, TaskStepStatus.SKIPPED} for step in steps
        ):
            raise ValueError("completed scratch delivery has unfinished execution steps")
        conversation = unit_of_work.state.get_conversation(task.conversation_id)
        workspace = unit_of_work.state.get_workspace(task.workspace_id)
        version = unit_of_work.state.get_version(task.target_version_id)
        if (
            conversation is None
            or conversation.workspace_type is not WorkspaceType.CHAT_SCRATCH
            or workspace is None
            or version is None
            or conversation.active_draft_version_id != task.target_version_id
        ):
            raise ValueError("scratch candidate no longer matches the active Turn Scope")
        preview = unit_of_work.state.preview_for_task(task.id, include_terminal=True)
        if preview is not None:
            if (
                preview.version_id != task.target_version_id
                or preview.status is not PreviewStatus.READY
            ):
                raise ValueError("scratch candidate Preview is not ready for promotion")
            workspace.active_preview_id = preview.id
            conversation.active_preview_id = preview.id
        workspace.accept_version(
            task.target_version_id,
            expected_revision=workspace.revision,
        )
        version.visibility = VersionVisibility.PROJECT_ACTIVE
        conversation.base_version_id = task.target_version_id
        conversation.active_draft_version_id = None
        conversation.active_task_id = None
        unit_of_work.state.save_workspace(workspace)
        unit_of_work.state.save_version(version)
        unit_of_work.state.save_conversation(conversation)
        unit_of_work.commands.append_event(
            run_id=run.id,
            event_type="workspace.version.auto_promoted",
            visibility=EventVisibility.USER,
            message="Workspace saved",
            payload={
                "workspace_id": str(task.workspace_id),
                "version_id": str(task.target_version_id),
                "turn_id": str(turn.id),
            },
            lease_owner=run.lease_owner,
            lease_fence=run.lease_fence,
        )

    @staticmethod
    def _release_failed_scratch_draft(unit_of_work, task) -> None:
        if task.project_id is not None:
            return
        conversation = unit_of_work.state.get_conversation(task.conversation_id)
        if conversation is None or conversation.workspace_type is not WorkspaceType.CHAT_SCRATCH:
            return
        changed = False
        if conversation.active_draft_version_id == task.target_version_id:
            conversation.active_draft_version_id = None
            changed = True
        if conversation.active_task_id == task.id:
            conversation.active_task_id = None
            changed = True
        preview = unit_of_work.state.preview_for_task(task.id, include_terminal=True)
        if preview is not None and conversation.active_preview_id == preview.id:
            conversation.active_preview_id = None
            changed = True
        if changed:
            unit_of_work.state.save_conversation(conversation)

    def _command_bus(self, ledger) -> CommandBus:
        return CommandBus(
            registry=self._registry,
            policy=self._policy,
            ledger=ledger,
        )


def _evidence_completion_issue(
    turn: AssistantTurn,
    invocations,
    cited_values: tuple[str, ...] | None,
) -> str | None:
    requirements = (
        turn.routing_decision.evidence_requirements if turn.routing_decision is not None else ()
    )
    if not requirements and cited_values is None:
        return None
    if cited_values is None:
        return (
            "EVIDENCE_CITATION_REQUIRED: This Turn requires governed evidence. Call a suitable "
            "read-only tool, then finish with direct_answer and cite the returned receipt IDs."
        )
    if not cited_values:
        if requirements:
            return (
                "EVIDENCE_CITATION_REQUIRED: direct_answer must cite at least one Evidence "
                "Receipt produced by this Turn."
            )
        return None
    try:
        cited_ids = tuple(UUID(value) for value in cited_values)
    except (TypeError, ValueError):
        return "EVIDENCE_CITATION_INVALID: Evidence Receipt IDs must be valid UUIDs."
    if len(cited_ids) != len(set(cited_ids)) or len(cited_ids) > 32:
        return "EVIDENCE_CITATION_INVALID: Evidence Receipt IDs must be unique and bounded."
    receipts = {
        receipt.id: receipt
        for invocation in invocations
        if invocation.status is ToolInvocationStatus.COMPLETED
        for receipt in invocation.evidence_receipts
    }
    cited = []
    now = datetime.now(UTC)
    for receipt_id in cited_ids:
        receipt = receipts.get(receipt_id)
        if receipt is None:
            return (
                "EVIDENCE_CITATION_INVALID: Every cited receipt must come from a successful "
                "tool call in this Turn."
            )
        if (
            receipt.turn_id != turn.id
            or receipt.task_id != turn.task_id
            or receipt.conversation_id != turn.conversation_id
            or receipt.scope_digest != turn.scope_digest
        ):
            return "EVIDENCE_SCOPE_MISMATCH: A cited receipt does not match this Turn Scope."
        if receipt.expires_at is not None and receipt.expires_at <= now:
            return (
                "EVIDENCE_EXPIRED: A cited current-state receipt expired. Refresh the relevant "
                "source before answering."
            )
        cited.append(receipt)
    covered = {receipt.requirement_kind for receipt in cited}
    missing = [requirement.value for requirement in requirements if requirement not in covered]
    if missing:
        return (
            "EVIDENCE_REQUIREMENT_UNSATISFIED: Gather and cite current evidence for: "
            + ", ".join(missing)
            + "."
        )
    return None


_LOOPBACK_URL = re.compile(
    r"https?://(?:localhost|127(?:\.[0-9]{1,3}){3})(?::[0-9]{1,5})?\S*",
    re.IGNORECASE,
)
_FENCED_CODE = re.compile(r"```[^\n]*\n(?P<body>.*?)```", re.DOTALL)


def _response_contract_issue(
    candidate_content: str | None,
    manifest: dict[str, object],
) -> str | None:
    if candidate_content is None:
        return None
    content = candidate_content.strip()
    if _LOOPBACK_URL.search(content):
        return (
            "The candidate final response contains an untrusted loopback URL or port. Return a "
            "concise completion summary without localhost, 127.0.0.1, port numbers, or claimed "
            "runtime commands. State only that the verified Preview is available in the Workspace."
        )
    fenced_characters = sum(len(match.group("body")) for match in _FENCED_CODE.finditer(content))
    if len(content) <= 2_400 and fenced_characters <= 600:
        return None
    planned_files = [
        str(item.get("path"))
        for item in manifest.get("files", [])
        if isinstance(item, dict) and item.get("path")
    ]
    visible_files = ", ".join(planned_files[:10])
    instruction = (
        "The candidate final response repeats generated source or is too long for a completion "
        "summary. Return at most 1,200 characters: summarize what changed, name the durable files, "
        "state the validation/Preview result, and mention any real limitation. Do not include full "
        "file contents or large code fences."
    )
    if visible_files:
        instruction += f" Durable files: {visible_files}."
    return instruction


def _public_failure_detail(error_code: str) -> str:
    return {
        "PROVIDER_PROTOCOL_ERROR": (
            "The selected model returned an unusable response. No pending tool action was applied."
        ),
        "PROVIDER_TIMEOUT": "The selected model did not respond in time.",
        "PROVIDER_RATE_LIMITED": "The selected model is temporarily busy.",
        "PROVIDER_AUTHENTICATION_FAILED": "The configured model account could not be authorized.",
        "PROVIDER_CONTEXT_LENGTH_EXCEEDED": (
            "The request exceeded the selected model's context limit."
        ),
        "PROVIDER_CONTENT_REJECTED": "The selected model could not process this request.",
        "PROVIDER_NETWORK_ERROR": "The model connection was interrupted.",
        "EVIDENCE_CITATION_REQUIRED": "Fairy could not finish without cited current evidence.",
        "EVIDENCE_CITATION_INVALID": "Fairy rejected an invalid evidence citation.",
        "EVIDENCE_SCOPE_MISMATCH": "Fairy rejected evidence from a different Task Scope.",
        "EVIDENCE_EXPIRED": "The current-state evidence expired before Fairy could answer.",
        "EVIDENCE_REQUIREMENT_UNSATISFIED": (
            "Fairy could not gather every required evidence source for this answer."
        ),
        "EVIDENCE_CLASSIFICATION_FAILED": (
            "Fairy could not safely classify the evidence needed for this request."
        ),
        "WORKER_INTERRUPTED": "Fairy was interrupted before this step completed.",
    }.get(error_code, "Fairy could not complete this step.")


_ACTIVE_TASK_STATUSES = frozenset(
    {
        TaskStatus.PLANNING,
        TaskStatus.AWAITING_APPROVAL,
        TaskStatus.EXECUTING,
        TaskStatus.INSTALLING,
        TaskStatus.PREVIEWING,
        TaskStatus.REVIEWING,
        TaskStatus.REPAIRING,
    }
)


__all__ = ["AssistantTurnLifecycleMixin"]
