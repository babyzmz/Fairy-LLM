from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

from fairy_core.contracts.planning import (
    ExecutionPlanContextModel,
    ExecutionPlanCreateInput,
)
from fastapi import APIRouter


def install_planning_routes(
    router: APIRouter,
    invoke: Callable[[str, Mapping[str, Any]], dict[str, Any]],
) -> None:
    @router.post(
        "/execution-plans",
        operation_id="execution_plans.create",
        response_model=ExecutionPlanContextModel,
    )
    def create_execution_plan(request: ExecutionPlanCreateInput) -> dict[str, Any]:
        return invoke("execution_plans.create", request.model_dump(mode="json"))

    @router.get(
        "/tasks/{task_id}/execution-plan",
        operation_id="execution_plans.get",
        response_model=ExecutionPlanContextModel,
    )
    def get_execution_plan(task_id: UUID) -> dict[str, Any]:
        return invoke("execution_plans.get", {"task_id": str(task_id)})


__all__ = ["install_planning_routes"]
