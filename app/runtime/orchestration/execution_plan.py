from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Iterable


def _new_step_id() -> str:
    return f"step_{uuid.uuid4().hex[:10]}"


@dataclass(slots=True)
class ExecutionStep:
    capability: str
    slots: dict[str, str]
    step_index: int
    normalized_query: str
    retryable: bool = True
    step_id: str = field(default_factory=_new_step_id)
    depends_on: list[str] = field(default_factory=list)
    can_run_parallel: bool = False
    abort_group: str = ""
    branch_key: str = "main"
    dependency_mode: str = "success"
    execution_confidence_score: float = 0.72
    inserted_by: str = ""
    rewrite_source: str = ""
    alternative_capabilities: list[str] = field(default_factory=list)

    def clone_with(self, **changes: object) -> "ExecutionStep":
        payload = {
            "capability": self.capability,
            "slots": dict(self.slots),
            "step_index": self.step_index,
            "normalized_query": self.normalized_query,
            "retryable": self.retryable,
            "step_id": self.step_id,
            "depends_on": list(self.depends_on),
            "can_run_parallel": self.can_run_parallel,
            "abort_group": self.abort_group,
            "branch_key": self.branch_key,
            "dependency_mode": self.dependency_mode,
            "execution_confidence_score": self.execution_confidence_score,
            "inserted_by": self.inserted_by,
            "rewrite_source": self.rewrite_source,
            "alternative_capabilities": list(self.alternative_capabilities),
        }
        payload.update(changes)
        return ExecutionStep(**payload)


@dataclass(slots=True)
class ExecutionPlan:
    steps: list[ExecutionStep] = field(default_factory=list)
    allow_partial_failure: bool = True
    continuation_of_plan_id: str = ""
    graph_mode: str = "linear"
    parallel_enabled: bool = False

    def step_map(self) -> dict[str, ExecutionStep]:
        return {step.step_id: step for step in self.steps}

    def ordered_steps(self) -> list[ExecutionStep]:
        return sorted(self.steps, key=lambda item: item.step_index)

    def pending_steps(self, completed: set[str], failed: set[str], skipped: set[str]) -> list[ExecutionStep]:
        done = completed | failed | skipped
        return [step for step in self.ordered_steps() if step.step_id not in done]

    def runnable_steps(
        self,
        *,
        completed: set[str],
        failed: set[str],
        skipped: set[str],
    ) -> list[ExecutionStep]:
        pending = self.pending_steps(completed, failed, skipped)
        return [
            step
            for step in pending
            if all(
                dependency in completed
                or dependency in skipped
                or (step.dependency_mode == "settled" and dependency in failed)
                for dependency in step.depends_on
            )
        ]

    def blocked_by_failed_dependencies(
        self,
        *,
        completed: set[str],
        failed: set[str],
        skipped: set[str],
    ) -> list[ExecutionStep]:
        done = completed | failed | skipped
        blocked: list[ExecutionStep] = []
        for step in self.ordered_steps():
            if step.step_id in done:
                continue
            if step.dependency_mode != "settled" and any(dependency in failed for dependency in step.depends_on):
                blocked.append(step)
        return blocked

    def next_parallel_batch(
        self,
        *,
        completed: set[str],
        failed: set[str],
        skipped: set[str],
    ) -> list[ExecutionStep]:
        runnable = self.runnable_steps(completed=completed, failed=failed, skipped=skipped)
        if not runnable:
            return []
        if not self.parallel_enabled:
            return [runnable[0]]
        primary = runnable[0]
        if not primary.can_run_parallel:
            return [primary]
        batch: list[ExecutionStep] = []
        for step in runnable:
            if not step.can_run_parallel:
                continue
            if primary.abort_group and step.abort_group and step.abort_group != primary.abort_group:
                continue
            batch.append(step)
        return batch or [primary]

    def append_steps(self, new_steps: Iterable[ExecutionStep]) -> None:
        start_index = len(self.steps)
        for offset, step in enumerate(new_steps):
            if not step.step_id:
                step.step_id = _new_step_id()
            step.step_index = start_index + offset
            self.steps.append(step)
