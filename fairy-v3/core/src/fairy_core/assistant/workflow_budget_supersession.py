from fairy_core.assistant.models import AssistantTurnStatus
from fairy_core.assistant.workflow_step_nodes import STEP_ROUTE
from fairy_core.assistant.workflow_supersession import (
    close_unstarted_approval,
    pending_approvals_owned,
)
from fairy_core.commanding.models import CommandStatus
from fairy_core.domain.execution import ApprovalDecision
from fairy_core.domain.models import TaskStatus
from fairy_core.workflow.models import WorkflowNodeStatus, WorkflowRunStatus


def unstarted_budget_approval(unit, snapshot, turn):
    if (
        snapshot.run.engine_version != 4 or turn.execution_engine_version != 4
        or snapshot.run.status is not WorkflowRunStatus.WAITING_FOR_APPROVAL
        or turn.workflow_run_id != snapshot.run.id or str(turn.id) != snapshot.run.owner_id
        or turn.status is not AssistantTurnStatus.WAITING_FOR_TOOL
        or turn.budget_approval_run_id is None
    ):
        return None
    waiting = tuple(node for node in snapshot.nodes if (
        node.plan_revision == snapshot.run.active_plan_revision
        and node.status is WorkflowNodeStatus.WAITING_FOR_APPROVAL
    ))
    if not waiting or any(node.kind != STEP_ROUTE for node in waiting):
        return None
    command = unit.commands.get_run(turn.budget_approval_run_id)
    if (
        command is None or command.command_name != "model.generate.expensive"
        or command.input_payload.get("turn_id") != str(turn.id)
        or command.task_id != turn.task_id or command.conversation_id != turn.conversation_id
        or command.scope_digest != turn.scope_digest
        or command.status not in {CommandStatus.WAITING_APPROVAL, CommandStatus.QUEUED}
    ):
        return None
    approval = unit.state.find_approval_by_command_run_id(command.id)
    expected = (ApprovalDecision.PENDING if command.status is CommandStatus.WAITING_APPROVAL
                else ApprovalDecision.APPROVED)
    if (
        approval is None or approval.task_id != turn.task_id
        or approval.tool_invocation_id is not None or approval.changeset_id is not None
        or approval.decision is not expected
        or not pending_approvals_owned(unit, turn, {approval.id})
    ):
        return None
    return command, approval


def supersede_budget_approval(unit, turn, record) -> None:
    command, approval = record
    close_unstarted_approval(unit, command, approval)
    task = unit.state.get_task(turn.task_id)
    if task.status is TaskStatus.AWAITING_APPROVAL:
        task.transition_to(TaskStatus.EXECUTING)
        unit.state.save_task(task)
    expected_status = turn.status
    turn.resume()
    turn.invalidate_routing_for_steering()
    unit.assistant.update_turn(
        turn, expected_status=expected_status,
        expected_cancellation_revision=turn.cancellation_revision,
    )
