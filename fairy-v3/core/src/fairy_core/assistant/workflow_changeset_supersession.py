import json
from uuid import UUID

from fairy_core.assistant.models import AssistantTurnStatus, ToolInvocationStatus
from fairy_core.assistant.trace_models import TraceStepKind, TraceStepStatus
from fairy_core.assistant.trace_runtime import TurnTraceRuntime
from fairy_core.assistant.workflow_supersession import (
    close_unstarted_approval,
    pending_approvals_owned,
)
from fairy_core.assistant.workflow_tool_plan import ASSISTANT_STEP_JOIN_KIND
from fairy_core.commanding.models import CommandStatus
from fairy_core.domain.execution import ApprovalDecision, ChangesetStatus
from fairy_core.domain.models import TaskStatus
from fairy_core.execution.plans import TaskStepKind, TaskStepStatus
from fairy_core.workflow.models import WorkflowNodeStatus, WorkflowRunStatus


def unstarted_changeset_approval(unit, snapshot, turn):
    if (
        snapshot.run.engine_version != 4
        or turn.execution_engine_version != 4
        or snapshot.run.status is not WorkflowRunStatus.WAITING_FOR_APPROVAL
        or turn.workflow_run_id != snapshot.run.id
        or str(turn.id) != snapshot.run.owner_id
        or turn.status is not AssistantTurnStatus.WAITING_FOR_TOOL
        or turn.budget_approval_run_id is not None
    ):
        return None
    waiting = tuple(
        node
        for node in snapshot.nodes
        if (
            node.plan_revision == snapshot.run.active_plan_revision
            and node.status is WorkflowNodeStatus.WAITING_FOR_APPROVAL
        )
    )
    if len(waiting) != 1 or waiting[0].kind != ASSISTANT_STEP_JOIN_KIND:
        return None
    join = waiting[0]
    if (
        join.payload.get("turn_id") != str(turn.id)
        or join.payload.get("scope_digest") != turn.scope_digest
    ):
        return None
    candidates = [
        item
        for item in unit.state.changesets_for_task(turn.task_id)
        if item.status
        in {ChangesetStatus.PROPOSED, ChangesetStatus.AWAITING_APPROVAL, ChangesetStatus.APPLYING}
    ]
    if len(candidates) != 1 or candidates[0].status is not ChangesetStatus.AWAITING_APPROVAL:
        return None
    changeset = candidates[0]
    task = unit.state.get_task(turn.task_id)
    if (
        task is None
        or changeset.task_id != task.id
        or changeset.conversation_id != turn.conversation_id
        or changeset.workspace_id != task.workspace_id
        or changeset.version_id != task.target_version_id
    ):
        return None
    matches = []
    for key in join.payload.get("invocation_ids", []):
        invocation = unit.assistant.get_tool_invocation(UUID(key))
        if (
            invocation is None
            or invocation.turn_id != turn.id
            or invocation.task_id != turn.task_id
            or invocation.scope_digest != turn.scope_digest
            or invocation.status is not ToolInvocationStatus.COMPLETED
        ):
            return None
        if invocation.tool_name == "edit.propose_changeset":
            try:
                receipt = json.loads(invocation.model_content)
            except (TypeError, ValueError):
                return None
            if receipt.get("changeset_id") == str(changeset.id):
                matches.append((invocation, receipt))
    if len(matches) != 1:
        return None
    invocation, receipt = matches[0]
    approval = unit.state.get_approval(UUID(receipt["approval_id"]))
    command = unit.commands.get_run(approval.command_run_id) if approval else None
    proposal = (
        unit.commands.get_run(invocation.command_run_id) if invocation.command_run_id else None
    )
    if (
        approval is None
        or approval.task_id != turn.task_id
        or approval.changeset_id != changeset.id
        or command is None
        or command.command_name != "edit.apply_changeset"
        or command.task_id != turn.task_id
        or command.conversation_id != turn.conversation_id
        or command.scope_digest != turn.scope_digest
        or command.status not in {CommandStatus.WAITING_APPROVAL, CommandStatus.QUEUED}
        or proposal is None
        or proposal.status is not CommandStatus.SUCCEEDED
        or proposal.task_id != turn.task_id
        or proposal.scope_digest != turn.scope_digest
        or not pending_approvals_owned(unit, turn, {approval.id})
    ):
        return None
    expected = (
        ApprovalDecision.PENDING
        if command.status is CommandStatus.WAITING_APPROVAL
        else ApprovalDecision.APPROVED
    )
    if approval.decision is not expected or changeset.approval_decision is not expected:
        return None
    return changeset, approval, command, invocation, proposal


def supersede_changeset_approval(unit, turn, record) -> None:
    changeset, approval, command, invocation, proposal = record
    close_unstarted_approval(unit, command, approval)
    if changeset.approval_decision is ApprovalDecision.PENDING:
        changeset.record_approval(ApprovalDecision.REJECTED)
    changeset.transition_to(ChangesetStatus.REJECTED)
    unit.state.save_changeset(changeset)
    summary = "Not applied: file proposal superseded by updated task requirements."
    invocation.revise_completed_result(
        public_summary=summary,
        model_content=json.dumps(
            {
                "changeset_id": str(changeset.id),
                "approval_id": str(approval.id),
                "status": "rejected",
                "reason_code": "EXECUTION_INTENT_CHANGED",
                "files": list(changeset.files),
            },
            separators=(",", ":"),
        ),
    )
    unit.assistant.update_tool_invocation(
        invocation, expected_status=ToolInvocationStatus.COMPLETED
    )
    for kind in (TraceStepKind.TOOL, TraceStepKind.APPROVAL):
        TurnTraceRuntime.transition_command_step_in_unit(
            unit,
            run=proposal,
            kind=kind,
            status=TraceStepStatus.CANCELLED,
            public_summary=summary,
        )
    plan = unit.state.execution_plan_for_task(turn.task_id)
    if plan is not None:
        # Only the awaiting proposal's batch is known not to have executed.
        batches = {
            item["batch"] for item in plan.manifest["files"] if item["path"] in changeset.files
        }
        implementation = [
            step
            for step in unit.state.task_steps_for_plan(plan.id)
            if step.kind is TaskStepKind.IMPLEMENT
        ]
        for index, step in enumerate(implementation, start=1):
            if index in batches and step.status is TaskStepStatus.RUNNING:
                step.transition_to(TaskStepStatus.FAILED, error_code="EXECUTION_INTENT_CHANGED")
                unit.state.save_task_step(
                    step,
                    expected_status=TaskStepStatus.RUNNING,
                    expected_attempts=step.attempts,
                )
    task = unit.state.get_task(turn.task_id)
    if task.status is TaskStatus.AWAITING_APPROVAL:
        task.transition_to(TaskStatus.EXECUTING)
        unit.state.save_task(task)
