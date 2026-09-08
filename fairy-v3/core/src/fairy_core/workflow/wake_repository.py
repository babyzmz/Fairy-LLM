from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select, union_all

from fairy_core.storage.schema import workflow_attempts, workflow_nodes, workflow_runs


class WorkflowWakeRepositoryMixin:
    def _epoch(self, column: Any) -> Any:
        if self._connection.dialect.name == "sqlite":
            return (func.julianday(column) - 2440587.5) * 86400.0
        return func.extract("epoch", column)

    def next_wake_delay(self, *, now: datetime, maximum: float) -> float:
        if maximum <= 0:
            raise ValueError("Workflow wake maximum must be positive")
        # Three database aggregates, one round trip; never hydrate plans or histories.
        deadlines = union_all(
            select(func.min(self._epoch(workflow_nodes.c.available_at)).label("due"))
            .select_from(
                workflow_nodes.join(
                    workflow_runs,
                    and_(
                        workflow_runs.c.tenant_id == workflow_nodes.c.tenant_id,
                        workflow_runs.c.id == workflow_nodes.c.run_id,
                        workflow_runs.c.active_plan_revision == workflow_nodes.c.plan_revision,
                    ),
                )
            )
            .where(
                workflow_nodes.c.tenant_id == self._tenant_id,
                workflow_nodes.c.status == "ready",
                workflow_nodes.c.available_at > now,
                workflow_runs.c.status.in_(("queued", "running")),
                workflow_runs.c.pause_requested.is_(False),
            ),
            select(func.min(self._epoch(workflow_attempts.c.lease_until))).where(
                workflow_attempts.c.tenant_id == self._tenant_id,
                workflow_attempts.c.status == "running",
            ),
            select(
                func.min(
                    self._epoch(workflow_runs.c.created_at) + workflow_runs.c.max_duration_seconds,
                )
            ).where(
                workflow_runs.c.tenant_id == self._tenant_id,
                workflow_runs.c.status.not_in(("completed", "cancelled", "failed")),
            ),
        ).subquery()
        due = self._connection.execute(select(func.min(deadlines.c.due))).scalar_one()
        return maximum if due is None else max(0.0, min(maximum, float(due) - now.timestamp()))
