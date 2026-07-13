from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fairy_core.contracts.planning import ExecutionPlanCreateInput
from fairy_core.domain.errors import IdempotencyConflictError
from fairy_core.execution.plans import (
    ExecutionPlan,
    TaskStep,
    TaskStepKind,
    TaskStepStatus,
)
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class ExecutionPlanContext:
    plan: ExecutionPlan
    steps: tuple[TaskStep, ...]


class ExecutionPlanningApplication:
    def __init__(self, unit_of_work_factory: CoreUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def create(
        self,
        request: ExecutionPlanCreateInput,
        *,
        initial_model_calls: int = 0,
        initial_tool_calls: int = 0,
    ) -> ExecutionPlanContext:
        manifest = {
            "files": [item.model_dump(mode="json") for item in request.files],
            "entrypoints": list(request.entrypoints),
            "dependencies": list(request.dependencies),
            "validation_commands": list(request.validation_commands),
        }
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(request.task_id)
            if task is None:
                raise KeyError(f"Task not found: {request.task_id}")
            existing = unit_of_work.state.execution_plan_for_task(task.id)
            if existing is not None:
                if dict(existing.manifest) != manifest:
                    raise IdempotencyConflictError("Task already has a different Execution Plan")
                return ExecutionPlanContext(
                    plan=existing,
                    steps=tuple(unit_of_work.state.task_steps_for_plan(existing.id)),
                )
            plan, steps = ExecutionPlan.create(
                task=task,
                manifest=manifest,
                initial_model_calls=initial_model_calls,
                initial_tool_calls=initial_tool_calls,
            )
            unit_of_work.state.save_execution_plan(plan, steps)
            unit_of_work.commit()
        return ExecutionPlanContext(plan=plan, steps=steps)

    def get_for_task(self, task_id: UUID) -> ExecutionPlanContext:
        with self._unit_of_work_factory() as unit_of_work:
            plan = unit_of_work.state.execution_plan_for_task(task_id)
            if plan is None:
                raise KeyError(f"Execution Plan not found for Task: {task_id}")
            steps = tuple(unit_of_work.state.task_steps_for_plan(plan.id))
        return ExecutionPlanContext(plan=plan, steps=steps)

    def start_file_batch(self, task_id: UUID, paths: tuple[str, ...]) -> TaskStep:
        with self._unit_of_work_factory() as unit_of_work:
            plan = unit_of_work.state.execution_plan_for_task(task_id)
            if plan is None:
                raise ValueError("Execution Plan is required before file changes")
            batch = self._batch_for_paths(plan, paths)
            steps = unit_of_work.state.task_steps_for_plan(plan.id)
            implementation_steps = [step for step in steps if step.kind is TaskStepKind.IMPLEMENT]
            step = implementation_steps[batch - 1]
            for previous in implementation_steps[: batch - 1]:
                if previous.status is not TaskStepStatus.COMPLETED:
                    raise ValueError("Execution Plan file batches must run in order")
            if step.status is TaskStepStatus.COMPLETED:
                return step
            if step.status is not TaskStepStatus.RUNNING:
                expected_status = step.status
                expected_attempts = step.attempts
                step.transition_to(TaskStepStatus.RUNNING)
                unit_of_work.state.save_task_step(
                    step,
                    expected_status=expected_status,
                    expected_attempts=expected_attempts,
                )
                unit_of_work.commit()
            return step

    def complete_file_batch(self, task_id: UUID, paths: tuple[str, ...]) -> TaskStep:
        return self._finish_file_batch(task_id, paths, error_code=None)

    def complete_file_batch_if_planned(
        self,
        task_id: UUID,
        paths: tuple[str, ...],
    ) -> TaskStep | None:
        with self._unit_of_work_factory() as unit_of_work:
            if unit_of_work.state.execution_plan_for_task(task_id) is None:
                return None
        return self.complete_file_batch(task_id, paths)

    def fail_file_batch(
        self,
        task_id: UUID,
        paths: tuple[str, ...],
        *,
        error_code: str,
    ) -> TaskStep:
        return self._finish_file_batch(task_id, paths, error_code=error_code)

    def start_step(self, task_id: UUID, kind: TaskStepKind) -> TaskStep | None:
        with self._unit_of_work_factory() as unit_of_work:
            plan = unit_of_work.state.execution_plan_for_task(task_id)
            if plan is None:
                return None
            steps = unit_of_work.state.task_steps_for_plan(plan.id)
            matches = [step for step in steps if step.kind is kind]
            if len(matches) != 1:
                raise ValueError(f"Execution Plan has no singular {kind.value} step")
            step = matches[0]
            if kind is TaskStepKind.TEST:
                install = next(item for item in steps if item.kind is TaskStepKind.INSTALL)
                if install.status is TaskStepStatus.PENDING and not plan.manifest.get(
                    "dependencies"
                ):
                    expected_status = install.status
                    expected_attempts = install.attempts
                    install.transition_to(TaskStepStatus.SKIPPED)
                    unit_of_work.state.save_task_step(
                        install,
                        expected_status=expected_status,
                        expected_attempts=expected_attempts,
                    )
                elif install.status not in {
                    TaskStepStatus.COMPLETED,
                    TaskStepStatus.SKIPPED,
                }:
                    raise ValueError("dependency installation must finish before tests")
            if kind in {TaskStepKind.INSTALL, TaskStepKind.TEST}:
                incomplete_batches = [
                    item
                    for item in steps
                    if item.kind is TaskStepKind.IMPLEMENT
                    and item.status is not TaskStepStatus.COMPLETED
                ]
                if incomplete_batches:
                    raise ValueError("all file batches must finish before validation")
            if step.status is TaskStepStatus.COMPLETED:
                return step
            if step.status is not TaskStepStatus.RUNNING:
                expected_status = step.status
                expected_attempts = step.attempts
                step.transition_to(TaskStepStatus.RUNNING)
                unit_of_work.state.save_task_step(
                    step,
                    expected_status=expected_status,
                    expected_attempts=expected_attempts,
                )
            unit_of_work.commit()
            return step

    def complete_step(self, task_id: UUID, kind: TaskStepKind) -> TaskStep | None:
        return self._finish_step(task_id, kind, error_code=None)

    def fail_step(
        self,
        task_id: UUID,
        kind: TaskStepKind,
        *,
        error_code: str,
    ) -> TaskStep | None:
        return self._finish_step(task_id, kind, error_code=error_code)

    def _finish_step(
        self,
        task_id: UUID,
        kind: TaskStepKind,
        *,
        error_code: str | None,
    ) -> TaskStep | None:
        with self._unit_of_work_factory() as unit_of_work:
            plan = unit_of_work.state.execution_plan_for_task(task_id)
            if plan is None:
                return None
            step = next(
                item
                for item in unit_of_work.state.task_steps_for_plan(plan.id)
                if item.kind is kind
            )
            if step.status is TaskStepStatus.COMPLETED and error_code is None:
                return step
            if step.status is not TaskStepStatus.RUNNING:
                raise ValueError(f"Execution Plan {kind.value} step is not running")
            expected_status = step.status
            expected_attempts = step.attempts
            step.transition_to(
                TaskStepStatus.FAILED if error_code is not None else TaskStepStatus.COMPLETED,
                error_code=error_code,
            )
            unit_of_work.state.save_task_step(
                step,
                expected_status=expected_status,
                expected_attempts=expected_attempts,
            )
            unit_of_work.commit()
            return step

    def _finish_file_batch(
        self,
        task_id: UUID,
        paths: tuple[str, ...],
        *,
        error_code: str | None,
    ) -> TaskStep:
        with self._unit_of_work_factory() as unit_of_work:
            plan = unit_of_work.state.execution_plan_for_task(task_id)
            if plan is None:
                raise ValueError("Execution Plan is required before file changes")
            batch = self._batch_for_paths(plan, paths)
            step = [
                item
                for item in unit_of_work.state.task_steps_for_plan(plan.id)
                if item.kind is TaskStepKind.IMPLEMENT
            ][batch - 1]
            if step.status is TaskStepStatus.COMPLETED and error_code is None:
                return step
            if step.status is not TaskStepStatus.RUNNING:
                raise ValueError("Execution Plan file batch is not running")
            expected_status = step.status
            expected_attempts = step.attempts
            step.transition_to(
                TaskStepStatus.FAILED if error_code is not None else TaskStepStatus.COMPLETED,
                error_code=error_code,
            )
            unit_of_work.state.save_task_step(
                step,
                expected_status=expected_status,
                expected_attempts=expected_attempts,
            )
            unit_of_work.commit()
        return step

    @staticmethod
    def _batch_for_paths(plan: ExecutionPlan, paths: tuple[str, ...]) -> int:
        if not paths:
            raise ValueError("file batch cannot be empty")
        planned = {str(item["path"]): int(item["batch"]) for item in plan.manifest["files"]}
        try:
            batches = {planned[path] for path in paths}
        except KeyError as error:
            raise ValueError(
                f"file is not present in the Execution Plan: {error.args[0]}"
            ) from error
        if len(batches) != 1:
            raise ValueError("one Changeset cannot span multiple planned batches")
        batch = batches.pop()
        expected_paths = {path for path, planned_batch in planned.items() if planned_batch == batch}
        if set(paths) != expected_paths:
            raise ValueError("Changeset must contain the complete planned file batch")
        return batch


__all__ = ["ExecutionPlanContext", "ExecutionPlanningApplication"]
