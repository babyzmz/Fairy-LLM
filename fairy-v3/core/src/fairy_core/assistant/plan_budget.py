from __future__ import annotations

from uuid import UUID

from fairy_core.execution.plan_revisions import obsolete_file_plan
from fairy_core.persistence.unit_of_work import CoreUnitOfWork


def consume_model_budget(unit_of_work: CoreUnitOfWork, task_id: UUID) -> None:
    _consume_budget(unit_of_work, task_id, model=True)


def consume_tool_budget(unit_of_work: CoreUnitOfWork, task_id: UUID) -> None:
    _consume_budget(unit_of_work, task_id, model=False)


def _consume_budget(
    unit_of_work: CoreUnitOfWork,
    task_id: UUID,
    *,
    model: bool,
) -> None:
    plan = unit_of_work.state.execution_plan_for_task(task_id)
    if plan is None or obsolete_file_plan(unit_of_work, task_id, plan):
        return
    expected_revision = plan.revision
    if model:
        plan.consume_model_call()
    else:
        plan.consume_tool_call()
    unit_of_work.state.update_execution_plan(plan, expected_revision=expected_revision)


__all__ = ["consume_model_budget", "consume_tool_budget"]
