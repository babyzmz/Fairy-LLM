from uuid import UUID

from sqlalchemy import select

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.execution.plans import ExecutionPlanStatus, TaskStepKind, TaskStepStatus


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
    return run_id == plan.workflow_run_id and (
        revision > plan.workflow_plan_revision
        or (revision == plan.workflow_plan_revision and consumed_file_plan(unit, task, plan))
    )


def active_file_objective(unit, task):
    turns = unit.assistant.nonterminal_turns_for_tasks((task.id,))
    if len(turns) != 1 or turns[0].execution_engine_version != 4:
        return 0
    intent = unit.assistant.get_execution_intent(turns[0].id)
    return (intent.active_objective_index or 0) if intent is not None else 0


def consumed_file_plan(unit, task, plan):
    """Only a committed earlier objective certificate consumes a file plan."""
    from fairy_core.assistant.workflow_objectives import OBJECTIVE_COMPLETE, certificate_binding
    from fairy_core.storage.schema import workflow_nodes

    turns = unit.assistant.nonterminal_turns_for_tasks((task.id,))
    if len(turns) != 1 or turns[0].workflow_run_id != plan.workflow_run_id:
        return False
    intent = unit.assistant.get_execution_intent(turns[0].id)
    if intent is None or intent.active_objective_index is None:
        return False
    with unit.assistant._session.read() as connection:
        rows = (
            connection.execute(
                select(workflow_nodes.c.result)
                .where(
                    workflow_nodes.c.tenant_id == unit.assistant._tenant_id,
                    workflow_nodes.c.run_id == str(plan.workflow_run_id),
                    workflow_nodes.c.plan_revision == plan.workflow_plan_revision,
                    workflow_nodes.c.kind == OBJECTIVE_COMPLETE,
                    workflow_nodes.c.status == "succeeded",
                )
                .limit(17)
            )
            .scalars()
            .all()
        )
    if len(rows) > 16:
        raise InvalidTransitionError("File plan objective certificates exceed their bound")
    for result in rows:
        if not result or result.get("file_plan_id") != str(plan.id):
            continue
        index = result.get("objective_index")
        if (
            type(index) is int
            and plan.workflow_objective_index <= index < intent.active_objective_index
            and all(
                result.get(k) == v
                for k, v in certificate_binding(
                    intent,
                    plan.workflow_run_id,
                    plan.workflow_plan_revision,
                    index,
                ).items()
            )
        ):
            return True
    return False


def settle_consumed_file_plan(unit, task, plan):
    if plan.status is ExecutionPlanStatus.COMPLETED:
        return
    if plan.status is not ExecutionPlanStatus.ACTIVE:
        raise InvalidTransitionError("Previous file plan has an unresolved outcome")
    steps = unit.state.task_steps_for_plan(plan.id)
    deferred = [
        step
        for step in steps
        if task.project_id is not None
        and step.kind is TaskStepKind.CHECKPOINT
        and step.status is TaskStepStatus.PENDING
    ]
    if not steps or any(
        step not in deferred
        and step.status
        not in {
            TaskStepStatus.COMPLETED,
            TaskStepStatus.SKIPPED,
        }
        for step in steps
    ):
        raise InvalidTransitionError("Previous file plan must finish before another objective")
    for step in deferred:
        step.transition_to(TaskStepStatus.SKIPPED)
        step.title = "Checkpoint deferred to final Task review"
        unit.state.save_task_step(
            step, expected_status=TaskStepStatus.PENDING, expected_attempts=step.attempts
        )
    expected = plan.revision
    plan.complete()
    unit.state.update_execution_plan(plan, expected_revision=expected)
