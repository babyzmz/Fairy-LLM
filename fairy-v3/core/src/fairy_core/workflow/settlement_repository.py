from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, select, update

from fairy_core.storage.schema import (
    workflow_attempts,
    workflow_edges,
    workflow_nodes,
    workflow_runs,
)
from fairy_core.workflow.models import (
    WorkflowAttemptStatus,
    WorkflowNodeStatus,
    WorkflowRunStatus,
)
from fairy_core.workflow.repository_records import (
    run_from_row,
)

_RUN_TERMINAL = {
    WorkflowRunStatus.COMPLETED,
    WorkflowRunStatus.CANCELLED,
    WorkflowRunStatus.FAILED,
}
_NODE_TERMINAL = {
    WorkflowNodeStatus.SUCCEEDED,
    WorkflowNodeStatus.FAILED,
    WorkflowNodeStatus.CANCELLED,
    WorkflowNodeStatus.SKIPPED,
    WorkflowNodeStatus.SUPERSEDED,
}


class WorkflowSettlementRepositoryMixin:
    def _promote_dependents(self, run_id: UUID, revision: int, *, now: datetime) -> None:
        parent = workflow_nodes.alias("dependency_parent")
        scope = (
            workflow_edges.c.tenant_id == self._tenant_id,
            workflow_edges.c.run_id == str(run_id),
            workflow_edges.c.plan_revision == revision,
            workflow_edges.c.to_node_id == workflow_nodes.c.id,
        )
        has_parent = (
            select(workflow_edges.c.from_node_id).where(*scope).correlate(workflow_nodes).exists()
        )
        unfinished_parent = (
            select(workflow_edges.c.from_node_id)
            .join(
                parent,
                and_(
                    parent.c.tenant_id == workflow_edges.c.tenant_id,
                    parent.c.id == workflow_edges.c.from_node_id,
                ),
            )
            .where(*scope, parent.c.status != WorkflowNodeStatus.SUCCEEDED.value)
            .correlate(workflow_nodes)
            .exists()
        )
        self._connection.execute(
            update(workflow_nodes)
            .where(
                workflow_nodes.c.tenant_id == self._tenant_id,
                workflow_nodes.c.run_id == str(run_id),
                workflow_nodes.c.plan_revision == revision,
                workflow_nodes.c.status == WorkflowNodeStatus.PENDING.value,
                has_parent,
                ~unfinished_parent,
            )
            .values(status=WorkflowNodeStatus.READY.value, updated_at=now)
        )

    def _settle_run(self, run_id: UUID, *, now: datetime) -> None:
        run = run_from_row(self._locked_run(run_id))
        active_nodes = (
            self._connection.execute(
                select(workflow_nodes.c.status).where(
                    workflow_nodes.c.tenant_id == self._tenant_id,
                    workflow_nodes.c.run_id == str(run_id),
                    workflow_nodes.c.plan_revision == run.active_plan_revision,
                )
            )
            .scalars()
            .all()
        )
        if run.pause_requested and not self._has_running_attempt(run_id):
            status = WorkflowRunStatus.PAUSED
            completed_at = None
        elif active_nodes and all(
            WorkflowNodeStatus(value) in _NODE_TERMINAL for value in active_nodes
        ):
            if any(value == WorkflowNodeStatus.FAILED.value for value in active_nodes):
                status = WorkflowRunStatus.FAILED
            elif any(value == WorkflowNodeStatus.CANCELLED.value for value in active_nodes):
                status = WorkflowRunStatus.CANCELLED
            else:
                status = WorkflowRunStatus.COMPLETED
            completed_at = now
        elif any(value == WorkflowNodeStatus.RUNNING.value for value in active_nodes):
            status = WorkflowRunStatus.RUNNING
            completed_at = None
        elif any(value == WorkflowNodeStatus.WAITING_FOR_APPROVAL.value for value in active_nodes):
            status = WorkflowRunStatus.WAITING_FOR_APPROVAL
            completed_at = None
        elif any(value == WorkflowNodeStatus.WAITING_FOR_INPUT.value for value in active_nodes):
            status = WorkflowRunStatus.WAITING_FOR_INPUT
            completed_at = None
        else:
            status = WorkflowRunStatus.QUEUED
            completed_at = None
        self._connection.execute(
            update(workflow_runs)
            .where(
                workflow_runs.c.tenant_id == self._tenant_id,
                workflow_runs.c.id == str(run_id),
            )
            .values(status=status.value, updated_at=now, completed_at=completed_at)
        )

    def _settle_pause(self, run_id: UUID, *, now: datetime) -> None:
        if not self._has_running_attempt(run_id):
            self._connection.execute(
                update(workflow_runs)
                .where(
                    workflow_runs.c.tenant_id == self._tenant_id,
                    workflow_runs.c.id == str(run_id),
                    workflow_runs.c.pause_requested.is_(True),
                    workflow_runs.c.status.not_in(tuple(status.value for status in _RUN_TERMINAL)),
                )
                .values(status=WorkflowRunStatus.PAUSED.value, updated_at=now)
            )

    def _has_running_attempt(self, run_id: UUID) -> bool:
        return (
            self._connection.execute(
                select(workflow_attempts.c.node_id)
                .where(
                    workflow_attempts.c.tenant_id == self._tenant_id,
                    workflow_attempts.c.run_id == str(run_id),
                    workflow_attempts.c.status == WorkflowAttemptStatus.RUNNING.value,
                )
                .limit(1)
            ).first()
            is not None
        )

    def _cancel_running_attempts(
        self,
        run_id: UUID,
        *,
        now: datetime,
        except_node_id: UUID | None = None,
    ) -> None:
        statement = update(workflow_attempts).where(
            workflow_attempts.c.tenant_id == self._tenant_id,
            workflow_attempts.c.run_id == str(run_id),
            workflow_attempts.c.status == WorkflowAttemptStatus.RUNNING.value,
        )
        if except_node_id is not None:
            statement = statement.where(workflow_attempts.c.node_id != str(except_node_id))
        self._connection.execute(
            statement.values(
                status=WorkflowAttemptStatus.CANCELLED.value,
                lease_owner=None,
                lease_until=None,
                error_code="WORKFLOW_CANCELLED",
                finished_at=now,
            )
        )
