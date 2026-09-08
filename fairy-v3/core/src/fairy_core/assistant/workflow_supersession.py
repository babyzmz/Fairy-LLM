from __future__ import annotations

from uuid import UUID

from fairy_core.assistant.models import ToolInvocationStatus
from fairy_core.assistant.trace_models import TraceStepKind, TraceStepStatus
from fairy_core.assistant.trace_runtime import TurnTraceRuntime
from fairy_core.assistant.workflow_tool_plan import ASSISTANT_STEP_TOOL_KIND
from fairy_core.commanding.models import CommandStatus, EventVisibility
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.execution import ApprovalDecision
from fairy_core.domain.models import TaskStatus
from fairy_core.workflow.models import WorkflowNodeStatus

_UNSTARTED = {ToolInvocationStatus.CREATED, ToolInvocationStatus.QUEUED}
_COMMAND_UNSTARTED = {
    CommandStatus.WAITING_APPROVAL, CommandStatus.QUEUED,
    CommandStatus.REJECTED, CommandStatus.CANCELLED,
}
_SUMMARY = "Not executed: superseded by updated task requirements."


def can_supersede_tool_approval(unit, snapshot, turn) -> bool:
    if snapshot.run.engine_version != 4 or turn.budget_approval_run_id is not None:
        return False
    waiting = tuple(node for node in snapshot.nodes if (
        node.plan_revision == snapshot.run.active_plan_revision
        and node.status is WorkflowNodeStatus.WAITING_FOR_APPROVAL
    ))
    if not waiting or any(node.kind != ASSISTANT_STEP_TOOL_KIND for node in waiting):
        return False
    candidates = _unstarted_calls(unit, snapshot, turn)
    ids = {str(invocation.id) for invocation, _, _ in candidates}
    if any(node.payload.get("invocation_id") not in ids for node in waiting):
        return False
    return _pending_approvals_owned(unit, turn, candidates)


def supersede_unstarted_tools(unit, snapshot, turn) -> None:
    """Close only this revision's undispatched calls in the plan transaction."""
    candidates = _unstarted_calls(unit, snapshot, turn)
    if not _pending_approvals_owned(unit, turn, candidates):
        raise InvalidTransitionError("A separate domain approval still requires a decision")
    for invocation, command, approval in candidates:
        if approval is not None and approval.decision is ApprovalDecision.PENDING:
            approval.decide(decision=ApprovalDecision.REJECTED, decided_by="core:steering")
            unit.state.update_approval(approval, expected_decision=ApprovalDecision.PENDING)
            unit.commands.append_event(
                run_id=command.id, event_type="approval.decided",
                visibility=EventVisibility.USER, message=_SUMMARY,
                payload={"approval_id": str(approval.id), "decision": "rejected",
                         "reason_code": "EXECUTION_INTENT_CHANGED"},
            )
        if command is not None:
            if command.status in {CommandStatus.WAITING_APPROVAL, CommandStatus.QUEUED}:
                unit.commands.transition(command.id, (
                    CommandStatus.REJECTED if command.status is CommandStatus.WAITING_APPROVAL
                    else CommandStatus.CANCELLED
                ))
                command = unit.commands.get_run(command.id)
            for kind in (TraceStepKind.TOOL, TraceStepKind.APPROVAL):
                TurnTraceRuntime.transition_command_step_in_unit(
                    unit, run=command, kind=kind, status=TraceStepStatus.CANCELLED,
                    public_summary=_SUMMARY,
                )
        expected_status = invocation.status
        invocation.reject(error_code="EXECUTION_INTENT_CHANGED", model_content=_SUMMARY)
        unit.assistant.update_tool_invocation(invocation, expected_status=expected_status)
    task = unit.state.get_task(turn.task_id)
    if candidates and task is not None and task.status is TaskStatus.AWAITING_APPROVAL:
        task.transition_to(TaskStatus.EXECUTING)
        unit.state.save_task(task)


def _unstarted_calls(unit, snapshot, turn):
    if (
        turn is None or turn.workflow_run_id != snapshot.run.id
        or str(turn.id) != snapshot.run.owner_id
        or turn.execution_engine_version != 4 or snapshot.run.engine_version != 4
    ):
        raise InvalidTransitionError("Tool supersession does not own this Workflow Turn")
    candidates = []
    for node in snapshot.nodes:
        if (
            node.plan_revision != snapshot.run.active_plan_revision
            or node.kind != ASSISTANT_STEP_TOOL_KIND
        ):
            continue
        invocation = unit.assistant.get_tool_invocation(UUID(node.payload["invocation_id"]))
        if (
            invocation is None or invocation.turn_id != turn.id
            or invocation.task_id != turn.task_id or invocation.scope_digest != turn.scope_digest
            or node.payload.get("turn_id") != str(turn.id)
            or node.payload.get("scope_digest") != turn.scope_digest
        ):
            raise InvalidTransitionError("Superseded tool belongs to a different task scope")
        if invocation.status not in _UNSTARTED:
            continue
        command = (
            unit.commands.get_run(invocation.command_run_id)
            if invocation.command_run_id is not None else None
        )
        if invocation.command_run_id is not None and (
            command is None or command.task_id != turn.task_id
            or command.conversation_id != turn.conversation_id
            or command.scope_digest != turn.scope_digest
            or command.command_name != invocation.tool_name
            or command.status not in _COMMAND_UNSTARTED
        ):
            raise InvalidTransitionError("Tool outcome must be reconciled before supersession")
        if invocation.status is ToolInvocationStatus.QUEUED and command is None:
            raise InvalidTransitionError("Queued tool has no matching command")
        approval = unit.state.find_approval_by_tool_invocation_id(invocation.id)
        if approval is not None and (
            command is None or approval.task_id != turn.task_id
            or approval.command_run_id != command.id or approval.changeset_id is not None
        ):
            raise InvalidTransitionError("Superseded approval belongs to a different command")
        candidates.append((invocation, command, approval))
    return candidates


def _pending_approvals_owned(unit, turn, candidates) -> bool:
    ids = {approval.id for _, _, approval in candidates if approval is not None}
    cursor = None
    while True:
        page = unit.state.list_approvals(
            project_id=None, conversation_id=turn.conversation_id,
            task_id=turn.task_id, limit=100, cursor=cursor,
        )
        if any(item.decision is ApprovalDecision.PENDING and item.id not in ids
               for item in page.items):
            return False
        cursor = page.next_cursor
        if cursor is None:
            return True
