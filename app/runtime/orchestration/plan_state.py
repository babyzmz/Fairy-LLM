from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .execution_plan import ExecutionStep


@dataclass(slots=True)
class StepExecutionResult:
    capability: str
    success: bool
    output_summary: str | None = None
    produced_entities: list[str] = field(default_factory=list)
    error_type: str | None = None
    skipped: bool = False
    retry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "success": self.success,
            "output_summary": self.output_summary,
            "produced_entities": list(self.produced_entities),
            "error_type": self.error_type,
            "skipped": self.skipped,
            "retry_count": self.retry_count,
        }


@dataclass(slots=True)
class ExecutionPlanState:
    plan_id: str
    steps: list[ExecutionStep] = field(default_factory=list)
    current_index: int = 0
    status: str = "running"
    results: list[StepExecutionResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "steps": [
                {
                    "capability": step.capability,
                    "slots": dict(step.slots),
                    "step_index": step.step_index,
                    "normalized_query": step.normalized_query,
                    "retryable": step.retryable,
                }
                for step in self.steps
            ],
            "current_index": self.current_index,
            "status": self.status,
            "results": [item.to_dict() for item in self.results],
        }
