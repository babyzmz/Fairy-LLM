from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, update

from fairy_core.domain.errors import VersionConflictError
from fairy_core.execution.plans import (
    ExecutionPlan,
    ExecutionPlanStatus,
    TaskStep,
    TaskStepKind,
    TaskStepStatus,
)
from fairy_core.storage.schema import execution_plans, task_steps


def _datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _optional_datetime(value: datetime | str | None) -> datetime | None:
    return _datetime(value) if value is not None else None


class PlanningStateStoreMixin:
    _tenant_id: str
    _session: Any

    def save_execution_plan(
        self,
        plan: ExecutionPlan,
        steps: Sequence[TaskStep],
    ) -> None:
        with self._session.write() as connection:
            connection.execute(
                insert(execution_plans).values(
                    tenant_id=self._tenant_id,
                    **self._plan_values(plan),
                )
            )
            connection.execute(
                insert(task_steps),
                [{"tenant_id": self._tenant_id, **self._step_values(step)} for step in steps],
            )

    def update_execution_plan(
        self,
        plan: ExecutionPlan,
        *,
        expected_revision: int,
    ) -> None:
        result = self._session_connection_update(
            execution_plans,
            plan.id,
            self._plan_values(plan),
            expected_revision=expected_revision,
        )
        if result != 1:
            raise VersionConflictError(
                f"expected Execution Plan revision {expected_revision}, current revision changed"
            )

    def get_execution_plan(self, plan_id: UUID) -> ExecutionPlan | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(execution_plans).where(
                        execution_plans.c.tenant_id == self._tenant_id,
                        execution_plans.c.id == str(plan_id),
                    )
                )
                .mappings()
                .first()
            )
        return self._plan_from_row(row) if row is not None else None

    def execution_plan_for_task(self, task_id: UUID) -> ExecutionPlan | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(execution_plans).where(
                        execution_plans.c.tenant_id == self._tenant_id,
                        execution_plans.c.task_id == str(task_id),
                    )
                )
                .mappings()
                .first()
            )
        return self._plan_from_row(row) if row is not None else None

    def task_steps_for_plan(self, plan_id: UUID) -> list[TaskStep]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(task_steps)
                    .where(
                        task_steps.c.tenant_id == self._tenant_id,
                        task_steps.c.plan_id == str(plan_id),
                    )
                    .order_by(task_steps.c.sequence)
                )
                .mappings()
                .all()
            )
        return [self._step_from_row(row) for row in rows]

    def save_task_step(
        self,
        step: TaskStep,
        *,
        expected_status: TaskStepStatus,
        expected_attempts: int,
    ) -> None:
        values = self._step_values(step)
        with self._session.write() as connection:
            result = connection.execute(
                update(task_steps)
                .where(
                    task_steps.c.tenant_id == self._tenant_id,
                    task_steps.c.id == str(step.id),
                    task_steps.c.status == expected_status.value,
                    task_steps.c.attempts == expected_attempts,
                )
                .values(
                    **{
                        key: value
                        for key, value in values.items()
                        if key not in {"id", "plan_id", "task_id", "sequence"}
                    }
                )
            )
        if result.rowcount != 1:
            raise VersionConflictError("Task Step state changed concurrently")

    def _session_connection_update(
        self,
        table,
        entity_id: UUID,
        values: Mapping[str, Any],
        *,
        expected_revision: int,
    ) -> int:
        with self._session.write() as connection:
            result = connection.execute(
                update(table)
                .where(
                    table.c.tenant_id == self._tenant_id,
                    table.c.id == str(entity_id),
                    table.c.revision == expected_revision,
                )
                .values(
                    **{
                        key: value
                        for key, value in values.items()
                        if key not in {"id", "task_id", "workspace_id", "version_id"}
                    }
                )
            )
        return int(result.rowcount or 0)

    @staticmethod
    def _plan_values(plan: ExecutionPlan) -> dict[str, Any]:
        return {
            "id": str(plan.id),
            "task_id": str(plan.task_id),
            "workspace_id": str(plan.workspace_id),
            "version_id": str(plan.version_id),
            "manifest": dict(plan.manifest),
            "status": plan.status.value,
            "max_model_calls": plan.max_model_calls,
            "max_tool_calls": plan.max_tool_calls,
            "max_repairs": plan.max_repairs,
            "max_duration_seconds": plan.max_duration_seconds,
            "model_calls_used": plan.model_calls_used,
            "tool_calls_used": plan.tool_calls_used,
            "repairs_used": plan.repairs_used,
            "revision": plan.revision,
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
        }

    @staticmethod
    def _step_values(step: TaskStep) -> dict[str, Any]:
        return {
            "id": str(step.id),
            "plan_id": str(step.plan_id),
            "task_id": str(step.task_id),
            "sequence": step.sequence,
            "kind": step.kind.value,
            "title": step.title,
            "status": step.status.value,
            "attempts": step.attempts,
            "error_code": step.error_code,
            "started_at": step.started_at,
            "completed_at": step.completed_at,
        }

    @staticmethod
    def _plan_from_row(row: Mapping[str, Any]) -> ExecutionPlan:
        return ExecutionPlan(
            id=UUID(row["id"]),
            task_id=UUID(row["task_id"]),
            workspace_id=UUID(row["workspace_id"]),
            version_id=UUID(row["version_id"]),
            manifest=MappingProxyType(dict(row["manifest"])),
            status=ExecutionPlanStatus(row["status"]),
            max_model_calls=int(row["max_model_calls"]),
            max_tool_calls=int(row["max_tool_calls"]),
            max_repairs=int(row["max_repairs"]),
            max_duration_seconds=int(row["max_duration_seconds"]),
            model_calls_used=int(row["model_calls_used"]),
            tool_calls_used=int(row["tool_calls_used"]),
            repairs_used=int(row["repairs_used"]),
            revision=int(row["revision"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _step_from_row(row: Mapping[str, Any]) -> TaskStep:
        return TaskStep(
            id=UUID(row["id"]),
            plan_id=UUID(row["plan_id"]),
            task_id=UUID(row["task_id"]),
            sequence=int(row["sequence"]),
            kind=TaskStepKind(row["kind"]),
            title=row["title"],
            status=TaskStepStatus(row["status"]),
            attempts=int(row["attempts"]),
            error_code=row["error_code"],
            started_at=_optional_datetime(row["started_at"]),
            completed_at=_optional_datetime(row["completed_at"]),
        )


__all__ = ["PlanningStateStoreMixin"]
