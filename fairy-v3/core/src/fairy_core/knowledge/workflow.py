from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock
from uuid import UUID

from fairy_core.contracts.common import ExecutionTarget
from fairy_core.domain.ids import new_id
from fairy_core.knowledge.models import KnowledgeSyncRun, KnowledgeSyncStatus
from fairy_core.knowledge.sync import ObsidianKnowledgeSync
from fairy_core.knowledge.work_queue import (
    KNOWLEDGE_SYNC_LEASE_DURATION,
    KnowledgeSyncClaim,
)
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import CancellationToken, ProviderCancelledError
from fairy_core.workflow.models import (
    WorkflowNode,
    WorkflowRun,
    WorkflowTriggerKind,
)
from fairy_core.workflow.scheduler import (
    WorkflowCancelled,
    WorkflowNodeResult,
    WorkflowRetryableError,
)

KNOWLEDGE_WORKFLOW_ENGINE_VERSION = 1
KNOWLEDGE_SYNC_NODE_KIND = "knowledge.sync.execute"


class KnowledgeWorkflowError(RuntimeError):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


def ensure_knowledge_workflow(unit_of_work, run: KnowledgeSyncRun):
    existing = unit_of_work.workflows.get_by_owner(
        owner_kind="knowledge_sync",
        owner_id=str(run.id),
        engine_version=KNOWLEDGE_WORKFLOW_ENGINE_VERSION,
    )
    if existing is not None:
        return existing
    workflow = WorkflowRun.create(
        owner_kind="knowledge_sync",
        owner_id=str(run.id),
        execution_target=ExecutionTarget.LOCAL,
        trigger_kind=WorkflowTriggerKind.DOMAIN,
        idempotency_key=f"knowledge-sync:{run.request_fingerprint}",
        project_id=run.project_id,
        engine_version=KNOWLEDGE_WORKFLOW_ENGINE_VERSION,
    )
    node = WorkflowNode.create(
        run_id=workflow.id,
        plan_revision=1,
        node_key="sync",
        kind=KNOWLEDGE_SYNC_NODE_KIND,
        payload={"sync_run_id": str(run.id)},
        public_summary="Synchronizing project knowledge",
        ready=True,
        resource_keys=(f"knowledge-source:{run.source_id}",),
        max_attempts=3,
    )
    return unit_of_work.workflows.create(workflow, nodes=(node,), edges=())


class KnowledgeSyncWorkflowAdapter:
    def __init__(
        self,
        *,
        application: ObsidianKnowledgeSync,
        unit_of_work_factory: CoreUnitOfWorkFactory,
    ) -> None:
        self._application = application
        self._unit_of_work_factory = unit_of_work_factory
        self._worker_id = f"knowledge-workflow:{new_id()}"
        self._claims: dict[UUID, KnowledgeSyncClaim] = {}
        self._lock = RLock()

    def heartbeat(self, node: WorkflowNode) -> bool:
        with self._lock:
            claim = self._claims.get(node.id)
        if claim is None:
            return False
        with self._unit_of_work_factory() as unit_of_work:
            renewed = unit_of_work.knowledge.renew_sync_run(
                claim,
                lease_until=datetime.now(UTC) + KNOWLEDGE_SYNC_LEASE_DURATION,
            )
            if renewed:
                unit_of_work.commit()
            return renewed

    def execute(
        self,
        node: WorkflowNode,
        cancellation: CancellationToken,
    ) -> WorkflowNodeResult:
        if node.kind != KNOWLEDGE_SYNC_NODE_KIND or set(node.payload) != {"sync_run_id"}:
            raise KnowledgeWorkflowError("KNOWLEDGE_WORKFLOW_PAYLOAD_INVALID")
        try:
            run_id = UUID(str(node.payload["sync_run_id"]))
        except (TypeError, ValueError) as error:
            raise KnowledgeWorkflowError("KNOWLEDGE_WORKFLOW_PAYLOAD_INVALID") from error
        with self._unit_of_work_factory() as unit_of_work:
            claim = unit_of_work.knowledge.claim_sync_run(
                run_id,
                worker_id=self._worker_id,
                lease_until=datetime.now(UTC) + KNOWLEDGE_SYNC_LEASE_DURATION,
            )
            if claim is not None:
                unit_of_work.commit()
        if claim is None:
            return self._settled_or_retry(run_id)
        with self._lock:
            self._claims[node.id] = claim
        try:
            run = self._application.execute(claim, cancellation)
        except ProviderCancelledError:
            if cancellation.is_interrupted:
                with self._unit_of_work_factory() as unit_of_work:
                    if unit_of_work.knowledge.abandon_sync_run(claim):
                        unit_of_work.commit()
            raise
        finally:
            with self._lock:
                self._claims.pop(node.id, None)
        return self._result(run)

    def _settled_or_retry(self, run_id: UUID) -> WorkflowNodeResult:
        run = self._application.get(run_id)
        if run.status in {
            KnowledgeSyncStatus.QUEUED,
            KnowledgeSyncStatus.RUNNING,
            KnowledgeSyncStatus.INTERRUPTED,
        }:
            raise WorkflowRetryableError(
                "Knowledge Sync is currently leased",
                error_code="KNOWLEDGE_SYNC_BUSY",
            )
        return self._result(run)

    @staticmethod
    def _result(run: KnowledgeSyncRun) -> WorkflowNodeResult:
        if run.status is KnowledgeSyncStatus.CANCELLED:
            raise WorkflowCancelled
        if run.status is KnowledgeSyncStatus.FAILED:
            raise KnowledgeWorkflowError(run.error_code or "KNOWLEDGE_SYNC_FAILED")
        if run.status is not KnowledgeSyncStatus.COMPLETED:
            raise KnowledgeWorkflowError("KNOWLEDGE_SYNC_UNSETTLED")
        return WorkflowNodeResult(
            output={
                "sync_run_id": str(run.id),
                "source_id": str(run.source_id),
                "scanned_count": run.scanned_count,
                "changed_count": run.changed_count,
                "deleted_count": run.deleted_count,
                "failed_count": run.failed_count,
            },
            evidence_refs=(f"knowledge-source:{run.source_id}:{run.source_cursor}",),
            public_summary=(
                f"Synchronized {run.scanned_count} knowledge item(s); {run.changed_count} changed"
            ),
        )


__all__ = [
    "KNOWLEDGE_SYNC_NODE_KIND",
    "KNOWLEDGE_WORKFLOW_ENGINE_VERSION",
    "KnowledgeSyncWorkflowAdapter",
    "KnowledgeWorkflowError",
    "ensure_knowledge_workflow",
]
