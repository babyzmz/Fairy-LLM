from __future__ import annotations

from uuid import UUID

from fairy_core.assistant.models import AssistantTurn
from fairy_core.assistant.plan_budget import consume_tool_budget
from fairy_core.assistant.routing import RoutingComplexity, RoutingDecision
from fairy_core.assistant.turn_reader import require_task, require_turn
from fairy_core.workflow.models import WorkflowBudget
from fairy_core.workflow.scheduler import WorkflowPaused


class AssistantWorkflowRuntimeMixin:
    def _configure_workflow_budget(
        self,
        turn_id: UUID,
        decision: RoutingDecision | None,
    ) -> WorkflowBudget:
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            task = require_task(unit_of_work, turn.task_id)
            if turn.workflow_run_id is None:
                return WorkflowBudget.normal()
            snapshot = unit_of_work.workflows.get(turn.workflow_run_id)
            if snapshot is None:
                raise RuntimeError("Assistant Workflow is unavailable")
            if decision is not None and decision.complexity is RoutingComplexity.HIGH:
                deep_budget = WorkflowBudget.deep()
                snapshot = unit_of_work.workflows.upgrade_budget(
                    turn.workflow_run_id,
                    budget=deep_budget,
                )
                execution_plan = unit_of_work.state.execution_plan_for_task(task.id)
                if execution_plan is not None:
                    expected_revision = execution_plan.revision
                    changed = execution_plan.upgrade_budget(
                        max_model_calls=deep_budget.max_model_rounds,
                        max_tool_calls=deep_budget.max_tool_invocations,
                        max_duration_seconds=deep_budget.max_duration_seconds,
                    )
                    if changed:
                        unit_of_work.state.update_execution_plan(
                            execution_plan,
                            expected_revision=expected_revision,
                        )
                unit_of_work.commit()
            return snapshot.run.budget

    def _raise_if_workflow_paused(self, turn_id: UUID) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            if turn.workflow_run_id is None:
                return
            snapshot = unit_of_work.workflows.get(turn.workflow_run_id)
        if snapshot is None:
            raise RuntimeError("Assistant Workflow is unavailable")
        if snapshot.run.pause_requested:
            raise WorkflowPaused

    @staticmethod
    def _reserve_workflow_budget_in_unit(
        unit_of_work,
        turn: AssistantTurn,
        *,
        model_rounds: int = 0,
        tool_invocations: int = 0,
    ) -> None:
        if turn.workflow_run_id is not None:
            unit_of_work.workflows.reserve_budget(
                turn.workflow_run_id,
                model_rounds=model_rounds,
                tool_invocations=tool_invocations,
            )

    def _reserve_parallel_tool_budget(self, turn_id: UUID, tool_count: int) -> None:
        if tool_count < 1:
            raise ValueError("parallel tool budget reservation must be positive")
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            task = require_task(unit_of_work, turn.task_id)
            self._reserve_workflow_budget_in_unit(
                unit_of_work,
                turn,
                tool_invocations=tool_count,
            )
            for _ in range(tool_count):
                consume_tool_budget(unit_of_work, task.id)
            unit_of_work.commit()


__all__ = ["AssistantWorkflowRuntimeMixin"]
