from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import case, select, update

from fairy_core.storage.schema import workflow_attempts, workflow_nodes, workflow_runs
from fairy_core.workflow.models import (
    WorkflowAttemptStatus,
    WorkflowNodeStatus,
    WorkflowRun,
    WorkflowRunStatus,
)
from fairy_core.workflow.time_queries import run_deadline_epoch

_DEADLINE_ERROR = "WORKFLOW_DEADLINE_EXCEEDED"
_TERMINAL_RUNS = {
    WorkflowRunStatus.COMPLETED.value,
    WorkflowRunStatus.CANCELLED.value,
    WorkflowRunStatus.FAILED.value,
}
_TERMINAL_NODES = {
    WorkflowNodeStatus.SUCCEEDED.value,
    WorkflowNodeStatus.FAILED.value,
    WorkflowNodeStatus.CANCELLED.value,
    WorkflowNodeStatus.SKIPPED.value,
    WorkflowNodeStatus.SUPERSEDED.value,
}


class WorkflowDeadlineRepositoryMixin:
    def _expire_overdue(
        self, now: datetime, *, on_failed: Callable[[WorkflowRun], None] | None = None,
        priority_run_id: UUID | None = None,
    ) -> None:
        runs = (
            self._connection.execute(
                select(workflow_runs.c.id).where(
                    workflow_runs.c.tenant_id == self._tenant_id,
                    workflow_runs.c.status.not_in(_TERMINAL_RUNS),
                    run_deadline_epoch(self._connection) <= now.timestamp(),
                ).order_by(
                    case((workflow_runs.c.id == str(priority_run_id), 0), else_=1),
                    workflow_runs.c.created_at, workflow_runs.c.id,
                ).limit(64).with_for_update(skip_locked=True)
            )
            .mappings()
            .all()
        )
        for run in runs:
            run_id = str(run["id"])
            self._connection.execute(
                update(workflow_attempts)
                .where(
                    workflow_attempts.c.tenant_id == self._tenant_id,
                    workflow_attempts.c.run_id == run_id,
                    workflow_attempts.c.status == WorkflowAttemptStatus.RUNNING.value,
                )
                .values(
                    status=WorkflowAttemptStatus.CANCELLED.value,
                    lease_owner=None,
                    lease_until=None,
                    error_code=_DEADLINE_ERROR,
                    finished_at=now,
                )
            )
            self._connection.execute(
                update(workflow_nodes)
                .where(
                    workflow_nodes.c.tenant_id == self._tenant_id,
                    workflow_nodes.c.run_id == run_id,
                    workflow_nodes.c.status.not_in(_TERMINAL_NODES),
                )
                .values(
                    status=WorkflowNodeStatus.CANCELLED.value,
                    error_code=_DEADLINE_ERROR,
                    updated_at=now,
                    completed_at=now,
                )
            )
            self._connection.execute(
                update(workflow_runs)
                .where(
                    workflow_runs.c.tenant_id == self._tenant_id,
                    workflow_runs.c.id == run_id,
                    workflow_runs.c.status.not_in(_TERMINAL_RUNS),
                )
                .values(
                    status=WorkflowRunStatus.FAILED.value,
                    pause_requested=False,
                    cancellation_revision=workflow_runs.c.cancellation_revision + 1,
                    error_code=_DEADLINE_ERROR,
                    updated_at=now,
                    completed_at=now,
                )
            )
            if on_failed is not None:
                on_failed(self.get_run(run["id"]))


__all__ = ["WorkflowDeadlineRepositoryMixin"]
