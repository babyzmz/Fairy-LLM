from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ExecutionStep:
    capability: str
    slots: dict[str, str]
    step_index: int
    normalized_query: str
    retryable: bool = True


@dataclass(slots=True)
class ExecutionPlan:
    steps: list[ExecutionStep] = field(default_factory=list)
    allow_partial_failure: bool = True
    continuation_of_plan_id: str = ""
