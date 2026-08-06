from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any
from uuid import UUID

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import Task


def _now() -> datetime:
    return datetime.now(UTC)


class ExecutionPlanStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStepKind(StrEnum):
    ANALYZE = "analyze"
    FILE_PLAN = "file_plan"
    IMPLEMENT = "implement"
    INSTALL = "install"
    TEST = "test"
    PREVIEW = "preview"
    REPAIR = "repair"
    SUMMARY = "summary"
    CHECKPOINT = "checkpoint"


class TaskStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


_STEP_TRANSITIONS = {
    TaskStepStatus.PENDING: frozenset({TaskStepStatus.RUNNING, TaskStepStatus.SKIPPED}),
    TaskStepStatus.RUNNING: frozenset({TaskStepStatus.COMPLETED, TaskStepStatus.FAILED}),
    TaskStepStatus.FAILED: frozenset({TaskStepStatus.RUNNING}),
    TaskStepStatus.COMPLETED: frozenset(),
    TaskStepStatus.SKIPPED: frozenset(),
}


@dataclass(slots=True)
class TaskStep:
    id: UUID
    plan_id: UUID
    task_id: UUID
    sequence: int
    kind: TaskStepKind
    title: str
    status: TaskStepStatus = TaskStepStatus.PENDING
    attempts: int = 0
    error_code: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        plan_id: UUID,
        task_id: UUID,
        sequence: int,
        kind: TaskStepKind,
        title: str,
        completed: bool = False,
    ) -> TaskStep:
        if sequence < 1:
            raise ValueError("Task Step sequence must be positive")
        normalized_title = title.strip()
        if not normalized_title or len(normalized_title) > 200:
            raise ValueError("Task Step title must contain 1 to 200 characters")
        now = _now()
        return cls(
            id=new_id(),
            plan_id=plan_id,
            task_id=task_id,
            sequence=sequence,
            kind=TaskStepKind(kind),
            title=normalized_title,
            status=TaskStepStatus.COMPLETED if completed else TaskStepStatus.PENDING,
            attempts=1 if completed else 0,
            started_at=now if completed else None,
            completed_at=now if completed else None,
        )

    def transition_to(self, status: TaskStepStatus, *, error_code: str | None = None) -> None:
        target = TaskStepStatus(status)
        if target not in _STEP_TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"cannot transition Task Step from {self.status.value} to {target.value}"
            )
        now = _now()
        if target is TaskStepStatus.RUNNING:
            self.attempts += 1
            self.started_at = now
            self.completed_at = None
            self.error_code = None
        elif target is TaskStepStatus.FAILED:
            normalized = (error_code or "WORKER_INTERRUPTED").strip()
            self.error_code = normalized[:128]
            self.completed_at = now
        elif target in {TaskStepStatus.COMPLETED, TaskStepStatus.SKIPPED}:
            self.completed_at = now
            self.error_code = None
        self.status = target


@dataclass(slots=True)
class ExecutionPlan:
    id: UUID
    task_id: UUID
    workspace_id: UUID
    version_id: UUID
    manifest: Mapping[str, Any]
    status: ExecutionPlanStatus = ExecutionPlanStatus.ACTIVE
    max_model_calls: int = 12
    max_tool_calls: int = 32
    max_repairs: int = 3
    max_duration_seconds: int = 1_800
    model_calls_used: int = 0
    tool_calls_used: int = 0
    repairs_used: int = 0
    revision: int = 0
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    @classmethod
    def create(
        cls,
        *,
        task: Task,
        manifest: Mapping[str, Any],
        initial_model_calls: int = 0,
        initial_tool_calls: int = 0,
    ) -> tuple[ExecutionPlan, tuple[TaskStep, ...]]:
        if task.workspace_id is None or task.target_version_id is None:
            raise ValueError("Execution Plan requires a Task Workspace Version")
        normalized = _normalize_manifest(manifest)
        plan = cls(
            id=new_id(),
            task_id=task.id,
            workspace_id=task.workspace_id,
            version_id=task.target_version_id,
            manifest=MappingProxyType(normalized),
            model_calls_used=initial_model_calls,
            tool_calls_used=initial_tool_calls,
        )
        if not 0 <= initial_model_calls <= plan.max_model_calls:
            raise ValueError("initial model usage exceeds the Execution Plan budget")
        if not 0 <= initial_tool_calls <= plan.max_tool_calls:
            raise ValueError("initial tool usage exceeds the Execution Plan budget")
        steps = _build_steps(plan, normalized)
        return plan, steps

    @property
    def deadline(self) -> datetime:
        return self.created_at + timedelta(seconds=self.max_duration_seconds)

    def consume_model_call(self) -> None:
        self._require_active()
        if self.model_calls_used >= self.max_model_calls or _now() >= self.deadline:
            self.pause()
            raise InvalidTransitionError("Execution Plan model budget exhausted")
        self.model_calls_used += 1
        self._touch()

    def consume_tool_call(self) -> None:
        self._require_active()
        if self.tool_calls_used >= self.max_tool_calls or _now() >= self.deadline:
            self.pause()
            raise InvalidTransitionError("Execution Plan tool budget exhausted")
        self.tool_calls_used += 1
        self._touch()

    def consume_repair(self) -> None:
        self._require_active()
        if self.repairs_used >= self.max_repairs:
            self.pause()
            raise InvalidTransitionError("Execution Plan repair budget exhausted")
        self.repairs_used += 1
        self._touch()

    def upgrade_budget(
        self,
        *,
        max_model_calls: int,
        max_tool_calls: int,
        max_duration_seconds: int,
    ) -> bool:
        self._require_active()
        current = (
            self.max_model_calls,
            self.max_tool_calls,
            self.max_duration_seconds,
        )
        requested = (max_model_calls, max_tool_calls, max_duration_seconds)
        if any(value < existing for value, existing in zip(requested, current, strict=True)):
            raise ValueError("Execution Plan budget cannot be reduced")
        if requested == current:
            return False
        self.max_model_calls, self.max_tool_calls, self.max_duration_seconds = requested
        self._touch()
        return True

    def pause(self) -> None:
        if self.status is not ExecutionPlanStatus.ACTIVE:
            raise InvalidTransitionError("only an active Execution Plan can pause")
        self.status = ExecutionPlanStatus.PAUSED
        self._touch()

    def complete(self) -> None:
        self._finish(ExecutionPlanStatus.COMPLETED)

    def fail(self) -> None:
        self._finish(ExecutionPlanStatus.FAILED)

    def cancel(self) -> None:
        self._finish(ExecutionPlanStatus.CANCELLED)

    def _finish(self, status: ExecutionPlanStatus) -> None:
        target = ExecutionPlanStatus(status)
        if target not in {
            ExecutionPlanStatus.COMPLETED,
            ExecutionPlanStatus.FAILED,
            ExecutionPlanStatus.CANCELLED,
        }:
            raise ValueError("Execution Plan finish status must be terminal")
        if self.status not in {ExecutionPlanStatus.ACTIVE, ExecutionPlanStatus.PAUSED}:
            raise InvalidTransitionError("Execution Plan is already terminal")
        self.status = target
        self._touch()

    def _require_active(self) -> None:
        if self.status is not ExecutionPlanStatus.ACTIVE:
            raise InvalidTransitionError("Execution Plan is not active")

    def _touch(self) -> None:
        self.revision += 1
        self.updated_at = _now()


def _normalize_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    try:
        normalized = json.loads(
            json.dumps(dict(manifest), ensure_ascii=True, allow_nan=False, sort_keys=True)
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Execution Plan manifest must be JSON-compatible") from error
    files = normalized.get("files")
    if not isinstance(files, list) or not files or len(files) > 200:
        raise ValueError("Execution Plan requires 1 to 200 planned files")
    seen_paths: set[str] = set()
    seen_batches: set[int] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("planned files must be objects")
        path = str(item.get("path", "")).strip()
        parsed = PurePosixPath(path)
        if not path or parsed.is_absolute() or ".." in parsed.parts or ":" in path:
            raise ValueError(f"invalid planned file path: {path}")
        if path in seen_paths:
            raise ValueError(f"duplicate planned file path: {path}")
        batch = item.get("batch")
        if isinstance(batch, bool) or not isinstance(batch, int) or batch < 1:
            raise ValueError("planned file batch must be a positive integer")
        seen_paths.add(path)
        seen_batches.add(batch)
    if seen_batches != set(range(1, max(seen_batches) + 1)):
        raise ValueError("planned file batches must be contiguous from 1")
    for batch in seen_batches:
        if sum(1 for item in files if item["batch"] == batch) > 25:
            raise ValueError("an implementation batch cannot exceed 25 files")
    return normalized


def _build_steps(plan: ExecutionPlan, manifest: Mapping[str, Any]) -> tuple[TaskStep, ...]:
    batches = max(int(item["batch"]) for item in manifest["files"])
    definitions: list[tuple[TaskStepKind, str, bool]] = [
        (TaskStepKind.ANALYZE, "Analyze request and Workspace", True),
        (TaskStepKind.FILE_PLAN, "Plan files and validation", True),
    ]
    definitions.extend(
        (TaskStepKind.IMPLEMENT, f"Implement file batch {batch}", False)
        for batch in range(1, batches + 1)
    )
    definitions.extend(
        [
            (TaskStepKind.INSTALL, "Install dependencies", False),
            (TaskStepKind.TEST, "Run validation suite", False),
            (TaskStepKind.PREVIEW, "Start and verify Preview", False),
            (TaskStepKind.REPAIR, "Repair validation failures", False),
            (TaskStepKind.SUMMARY, "Summarize completed work", False),
            (TaskStepKind.CHECKPOINT, "Create durable checkpoint", False),
        ]
    )
    return tuple(
        TaskStep.create(
            plan_id=plan.id,
            task_id=plan.task_id,
            sequence=sequence,
            kind=kind,
            title=title,
            completed=completed,
        )
        for sequence, (kind, title, completed) in enumerate(definitions, start=1)
    )


__all__ = [
    "ExecutionPlan",
    "ExecutionPlanStatus",
    "TaskStep",
    "TaskStepKind",
    "TaskStepStatus",
]
