from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import update

from fairy_core.storage.schema import workflow_runs
from fairy_core.workflow.errors import WorkflowBudgetExceeded
from fairy_core.workflow.models import (
    WorkflowBudget,
    WorkflowBudgetTier,
    WorkflowRunStatus,
    WorkflowSnapshot,
)
from fairy_core.workflow.repository_records import load_snapshot, run_from_row

_TERMINAL = {
    WorkflowRunStatus.COMPLETED,
    WorkflowRunStatus.CANCELLED,
    WorkflowRunStatus.FAILED,
}


class WorkflowBudgetRepositoryMixin:
    def reserve_budget(
        self,
        run_id: UUID,
        *,
        model_rounds: int = 0,
        tool_invocations: int = 0,
    ) -> WorkflowSnapshot:
        if model_rounds < 0 or tool_invocations < 0 or not (model_rounds or tool_invocations):
            raise ValueError("Workflow budget reservation must be positive")
        run = run_from_row(self._locked_run(run_id))
        if run.status in _TERMINAL:
            raise WorkflowBudgetExceeded("Terminal Workflow cannot reserve budget")
        next_model_rounds = run.model_rounds_used + model_rounds
        next_tool_invocations = run.tool_invocations_used + tool_invocations
        if (
            next_model_rounds > run.budget.max_model_rounds
            or next_tool_invocations > run.budget.max_tool_invocations
        ):
            raise WorkflowBudgetExceeded("Workflow execution budget is exhausted")
        reserved = self._connection.execute(
            update(workflow_runs)
            .where(
                workflow_runs.c.tenant_id == self._tenant_id,
                workflow_runs.c.id == str(run_id),
                workflow_runs.c.status.not_in(tuple(status.value for status in _TERMINAL)),
                workflow_runs.c.model_rounds_used + model_rounds
                <= workflow_runs.c.max_model_rounds,
                workflow_runs.c.tool_invocations_used + tool_invocations
                <= workflow_runs.c.max_tool_invocations,
            )
            .values(
                model_rounds_used=workflow_runs.c.model_rounds_used + model_rounds,
                tool_invocations_used=workflow_runs.c.tool_invocations_used + tool_invocations,
                updated_at=datetime.now(UTC),
            )
        )
        if reserved.rowcount != 1:
            raise WorkflowBudgetExceeded("Workflow budget changed before the call was admitted")
        snapshot = self.get(run_id)
        assert snapshot is not None
        return snapshot

    def upgrade_budget(
        self,
        run_id: UUID,
        *,
        budget: WorkflowBudget,
    ) -> WorkflowSnapshot:
        run = run_from_row(self._locked_run(run_id))
        if run.budget == budget:
            return load_snapshot(self._connection, self._tenant_id, run)
        if (
            run.budget.tier is not WorkflowBudgetTier.NORMAL
            or budget.tier is not WorkflowBudgetTier.DEEP
            or budget.max_model_rounds < run.budget.max_model_rounds
            or budget.max_tool_invocations < run.budget.max_tool_invocations
            or budget.max_duration_seconds < run.budget.max_duration_seconds
            or budget.max_parallel_nodes < run.budget.max_parallel_nodes
        ):
            raise ValueError("Workflow budget can only upgrade from normal to deep")
        if run.status in _TERMINAL:
            raise ValueError("Terminal Workflow budget cannot change")
        self._connection.execute(
            update(workflow_runs)
            .where(
                workflow_runs.c.tenant_id == self._tenant_id,
                workflow_runs.c.id == str(run_id),
            )
            .values(
                budget_tier=budget.tier.value,
                max_model_rounds=budget.max_model_rounds,
                max_tool_invocations=budget.max_tool_invocations,
                max_duration_seconds=budget.max_duration_seconds,
                max_parallel_nodes=budget.max_parallel_nodes,
                updated_at=datetime.now(UTC),
            )
        )
        snapshot = self.get(run_id)
        assert snapshot is not None
        return snapshot


__all__ = ["WorkflowBudgetRepositoryMixin"]
