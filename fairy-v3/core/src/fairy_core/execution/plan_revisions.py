from uuid import UUID

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.execution.plans import ExecutionPlanStatus, TaskStepStatus


def active_file_plan_binding(unit, task):
    """Resolve provenance from the owner, never from execution.plan model arguments."""
    turns = unit.assistant.nonterminal_turns_for_tasks((task.id,))
    if len(turns) > 1:
        raise InvalidTransitionError("Task has multiple active Assistant owners")
    if not turns or turns[0].execution_engine_version != 4:
        return None, None
    turn = turns[0]
    run = unit.workflows.get_run(turn.workflow_run_id) if turn.workflow_run_id else None
    if (
        run is None
        or run.engine_version != 4
        or run.owner_id != str(turn.id)
        or turn.task_id != task.id
        or turn.conversation_id != task.conversation_id
    ):
        raise InvalidTransitionError("File plan has no valid Workflow owner")
    return run.id, run.active_plan_revision


def supersede_file_plan(unit, turn, revision: int) -> None:
    plan = unit.state.execution_plan_for_task(turn.task_id)
    if plan is None or plan.status not in {ExecutionPlanStatus.ACTIVE, ExecutionPlanStatus.PAUSED}:
        return
    if plan.workflow_run_id not in {
        None,
        turn.workflow_run_id,
    } or plan.workflow_plan_revision not in {None, revision}:
        raise InvalidTransitionError("File plan belongs to a different Workflow revision")
    steps = unit.state.task_steps_for_plan(plan.id)
    if any(step.status is TaskStepStatus.RUNNING for step in steps):
        raise InvalidTransitionError("Active file work must reach a known outcome before steering")
    expected_revision = plan.revision
    plan.cancel()
    unit.state.update_execution_plan(plan, expected_revision=expected_revision)
    for step in steps:
        if step.status is TaskStepStatus.PENDING:
            step.transition_to(TaskStepStatus.SKIPPED)
            unit.state.save_task_step(
                step,
                expected_status=TaskStepStatus.PENDING,
                expected_attempts=step.attempts,
            )


def obsolete_file_plan(unit, task_id: UUID, plan) -> bool:
    if plan.workflow_run_id is None or plan.workflow_plan_revision is None:
        return False
    task = unit.state.get_task(task_id)
    if task is None:
        return False
    run_id, revision = active_file_plan_binding(unit, task)
    return run_id == plan.workflow_run_id and revision > plan.workflow_plan_revision
