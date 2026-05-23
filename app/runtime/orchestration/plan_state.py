from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .execution_plan import ExecutionStep


@dataclass(slots=True)
class StepExecutionResult:
    step_id: str
    capability: str
    success: bool
    output_summary: str | None = None
    produced_entities: list[str] = field(default_factory=list)
    error_type: str | None = None
    skipped: bool = False
    retry_count: int = 0
    execution_confidence_score: float = 0.0
    confidence_factors: list[str] = field(default_factory=list)
    branch_key: str = "main"
    inserted_steps: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "capability": self.capability,
            "success": self.success,
            "output_summary": self.output_summary,
            "produced_entities": list(self.produced_entities),
            "error_type": self.error_type,
            "skipped": self.skipped,
            "retry_count": self.retry_count,
            "execution_confidence_score": self.execution_confidence_score,
            "confidence_factors": list(self.confidence_factors),
            "branch_key": self.branch_key,
            "inserted_steps": list(self.inserted_steps),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ExecutionPlanState:
    plan_id: str
    steps: list[ExecutionStep] = field(default_factory=list)
    current_index: int = 0
    status: str = "running"
    results: list[StepExecutionResult] = field(default_factory=list)
    step_statuses: dict[str, str] = field(default_factory=dict)
    rewrite_log: list[dict[str, Any]] = field(default_factory=list)
    branch_statuses: dict[str, str] = field(default_factory=dict)
    active_batch: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.step_statuses and self.steps:
            self.step_statuses = {step.step_id: "pending" for step in self.steps}
        if not self.branch_statuses and self.steps:
            branch_keys = {step.branch_key or "main" for step in self.steps}
            self.branch_statuses = {branch: "pending" for branch in branch_keys}

    @property
    def completed_step_ids(self) -> set[str]:
        return {step_id for step_id, status in self.step_statuses.items() if status == "completed"}

    @property
    def failed_step_ids(self) -> set[str]:
        return {step_id for step_id, status in self.step_statuses.items() if status == "failed"}

    @property
    def skipped_step_ids(self) -> set[str]:
        return {step_id for step_id, status in self.step_statuses.items() if status == "skipped"}

    def mark_step_status(self, step_id: str, status: str) -> None:
        self.step_statuses[step_id] = status

    def sync_branch_statuses(self) -> None:
        branch_steps: dict[str, list[str]] = {}
        for step in self.steps:
            branch_steps.setdefault(step.branch_key or "main", []).append(step.step_id)
        for branch_key, step_ids in branch_steps.items():
            statuses = [self.step_statuses.get(step_id, "pending") for step_id in step_ids]
            if statuses and all(status == "completed" for status in statuses):
                self.branch_statuses[branch_key] = "completed"
            elif any(status == "failed" for status in statuses):
                self.branch_statuses[branch_key] = "failed"
            elif any(status == "running" for status in statuses):
                self.branch_statuses[branch_key] = "running"
            elif any(status == "cancelled" for status in statuses):
                self.branch_statuses[branch_key] = "cancelled"
            elif any(status == "skipped" for status in statuses) and all(
                status in {"skipped", "completed"} for status in statuses
            ):
                self.branch_statuses[branch_key] = "completed"
            else:
                self.branch_statuses[branch_key] = "pending"

    def append_steps(self, steps: list[ExecutionStep]) -> None:
        for step in steps:
            self.steps.append(step)
            self.step_statuses.setdefault(step.step_id, "pending")
            self.branch_statuses.setdefault(step.branch_key or "main", "pending")

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "steps": [
                {
                    "step_id": step.step_id,
                    "capability": step.capability,
                    "slots": dict(step.slots),
                    "step_index": step.step_index,
                    "normalized_query": step.normalized_query,
                    "retryable": step.retryable,
                    "depends_on": list(step.depends_on),
                    "can_run_parallel": step.can_run_parallel,
                    "abort_group": step.abort_group,
                    "branch_key": step.branch_key,
                    "dependency_mode": step.dependency_mode,
                    "execution_confidence_score": step.execution_confidence_score,
                    "inserted_by": step.inserted_by,
                    "rewrite_source": step.rewrite_source,
                    "alternative_capabilities": list(step.alternative_capabilities),
                }
                for step in self.steps
            ],
            "current_index": self.current_index,
            "status": self.status,
            "results": [item.to_dict() for item in self.results],
            "step_statuses": dict(self.step_statuses),
            "rewrite_log": list(self.rewrite_log),
            "branch_statuses": dict(self.branch_statuses),
            "active_batch": list(self.active_batch),
        }
