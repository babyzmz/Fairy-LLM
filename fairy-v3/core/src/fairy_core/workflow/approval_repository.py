from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update

from fairy_core.storage.schema import workflow_attempts, workflow_nodes, workflow_runs
from fairy_core.workflow.models import (
    WorkflowAttemptClaim,
    WorkflowAttemptStatus,
    WorkflowNodeStatus,
    WorkflowRunStatus,
    WorkflowSnapshot,
)


class WorkflowApprovalRepositoryMixin:
    def wait_for_approval(
        self,
        claim: WorkflowAttemptClaim,
        *,
        result: Mapping[str, Any],
    ) -> WorkflowSnapshot:
        now = datetime.now(UTC)
        self._require_claim(claim, now=now)
        self._connection.execute(
            update(workflow_attempts)
            .where(*self._claim_predicates(claim, require_live=True))
            .values(
                status=WorkflowAttemptStatus.WAITING.value,
                lease_owner=None,
                lease_until=None,
                result=dict(result),
                finished_at=now,
            )
        )
        self._connection.execute(
            update(workflow_nodes)
            .where(
                workflow_nodes.c.tenant_id == self._tenant_id,
                workflow_nodes.c.id == str(claim.node_id),
                workflow_nodes.c.status == WorkflowNodeStatus.RUNNING.value,
            )
            .values(
                status=WorkflowNodeStatus.WAITING_FOR_APPROVAL.value,
                result=dict(result),
                error_code=None,
                updated_at=now,
            )
        )
        self._connection.execute(
            update(workflow_runs)
            .where(
                workflow_runs.c.tenant_id == self._tenant_id,
                workflow_runs.c.id == str(claim.run_id),
            )
            .values(status=WorkflowRunStatus.WAITING_FOR_APPROVAL.value, updated_at=now)
        )
        snapshot = self.get(claim.run_id)
        assert snapshot is not None
        return snapshot


__all__ = ["WorkflowApprovalRepositoryMixin"]
