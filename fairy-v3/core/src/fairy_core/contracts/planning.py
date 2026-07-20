from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from fairy_core.contracts.common import ContractModel
from fairy_core.execution.plans import (
    ExecutionPlan,
    ExecutionPlanStatus,
    TaskStep,
    TaskStepKind,
    TaskStepStatus,
)


class PlannedFileInput(ContractModel):
    path: str = Field(min_length=1, max_length=1_024)
    purpose: str = Field(min_length=1, max_length=500)
    batch: int = Field(ge=1, le=200)
    expected_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("expected_hash")
    @classmethod
    def normalize_absent_file_hash(cls, value: str | None) -> str | None:
        # Some tool-capable models use a zero digest as the conventional create-file
        # sentinel. Canonicalize it at the contract boundary so plans remain stable.
        return None if value == "0" * 64 else value


class ExecutionPlanCreateInput(ContractModel):
    task_id: UUID
    files: tuple[PlannedFileInput, ...] = Field(min_length=1, max_length=200)
    entrypoints: tuple[str, ...] = Field(default=(), max_length=32)
    dependencies: tuple[str, ...] = Field(default=(), max_length=128)
    validation_commands: tuple[str, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_batches(self) -> ExecutionPlanCreateInput:
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("planned file paths must be unique")
        batches = {item.batch for item in self.files}
        if batches != set(range(1, max(batches) + 1)):
            raise ValueError("planned batches must be contiguous from 1")
        if any(sum(item.batch == batch for item in self.files) > 25 for batch in batches):
            raise ValueError("a planned batch cannot exceed 25 files")
        return self


class ExecutionPlanIdInput(ContractModel):
    task_id: UUID


class TaskStepModel(ContractModel):
    id: UUID
    plan_id: UUID
    task_id: UUID
    sequence: int
    kind: TaskStepKind
    title: str
    status: TaskStepStatus
    attempts: int
    error_code: str | None
    started_at: datetime | None
    completed_at: datetime | None

    @classmethod
    def from_domain(cls, step: TaskStep) -> TaskStepModel:
        return cls.model_validate(step, from_attributes=True)


class ExecutionPlanModel(ContractModel):
    id: UUID
    task_id: UUID
    workspace_id: UUID
    version_id: UUID
    manifest: dict[str, Any]
    status: ExecutionPlanStatus
    max_model_calls: int
    max_tool_calls: int
    max_repairs: int
    max_duration_seconds: int
    model_calls_used: int
    tool_calls_used: int
    repairs_used: int
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, plan: ExecutionPlan) -> ExecutionPlanModel:
        return cls(
            id=plan.id,
            task_id=plan.task_id,
            workspace_id=plan.workspace_id,
            version_id=plan.version_id,
            manifest=dict(plan.manifest),
            status=plan.status,
            max_model_calls=plan.max_model_calls,
            max_tool_calls=plan.max_tool_calls,
            max_repairs=plan.max_repairs,
            max_duration_seconds=plan.max_duration_seconds,
            model_calls_used=plan.model_calls_used,
            tool_calls_used=plan.tool_calls_used,
            repairs_used=plan.repairs_used,
            revision=plan.revision,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )


class ExecutionPlanContextModel(ContractModel):
    plan: ExecutionPlanModel
    steps: tuple[TaskStepModel, ...]

    @classmethod
    def from_domain(cls, context: Any) -> ExecutionPlanContextModel:
        return cls(
            plan=ExecutionPlanModel.from_domain(context.plan),
            steps=tuple(TaskStepModel.from_domain(step) for step in context.steps),
        )


__all__ = [
    "ExecutionPlanContextModel",
    "ExecutionPlanCreateInput",
    "ExecutionPlanIdInput",
    "ExecutionPlanModel",
    "PlannedFileInput",
    "TaskStepModel",
]
