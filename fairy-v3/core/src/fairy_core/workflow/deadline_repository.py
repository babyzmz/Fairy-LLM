from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, update

from fairy_core.storage.schema import workflow_attempts, workflow_nodes, workflow_runs
from fairy_core.workflow.models import (
    WorkflowAttemptStatus,
    WorkflowNodeStatus,
    WorkflowRunStatus,
)

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
    def _expire_overdue(self, now: datetime) -> None:
        runs = (
            self._connection.execute(
                select(
                    workflow_runs.c.id,
                    workflow_runs.c.created_at,
                    workflow_runs.c.max_duration_seconds,
                ).where(
                    workflow_runs.c.tenant_id == self._tenant_id,
                    workflow_runs.c.status.not_in(_TERMINAL_RUNS),
                )
            )
            .mappings()
            .all()
        )
        for run in runs:
            if run["created_at"] + timedelta(seconds=int(run["max_duration_seconds"])) > now:
                continue
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


__all__ = ["WorkflowDeadlineRepositoryMixin"]
