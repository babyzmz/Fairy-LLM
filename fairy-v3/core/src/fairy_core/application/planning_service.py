from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel

from fairy_core.contracts.planning import (
    ExecutionPlanContextModel,
    ExecutionPlanCreateInput,
    ExecutionPlanIdInput,
)
from fairy_core.execution.planning import ExecutionPlanningApplication


def planning_service_handlers(
    planning: ExecutionPlanningApplication,
) -> dict[str, Any]:
    def create(request: BaseModel) -> ExecutionPlanContextModel:
        context = planning.create(cast(ExecutionPlanCreateInput, request))
        return ExecutionPlanContextModel.from_domain(context)

    def get(request: BaseModel) -> ExecutionPlanContextModel:
        task_id = cast(ExecutionPlanIdInput, request).task_id
        return ExecutionPlanContextModel.from_domain(planning.get_for_task(task_id))

    return {"execution_plans.create": create, "execution_plans.get": get}


__all__ = ["planning_service_handlers"]
