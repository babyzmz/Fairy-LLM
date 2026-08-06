from __future__ import annotations

from uuid import UUID

from fairy_core.commanding.models import EventVisibility
from fairy_core.contracts.knowledge import KnowledgeSyncStartInput
from fairy_core.knowledge.models import KnowledgeSyncRun, KnowledgeSyncStatus
from fairy_core.knowledge.sync import ObsidianKnowledgeSync
from fairy_core.knowledge.workflow import (
    KNOWLEDGE_SYNC_NODE_KIND,
    KnowledgeSyncWorkflowAdapter,
    ensure_knowledge_workflow,
)
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.workflow.models import WorkflowRunStatus
from fairy_core.workflow.scheduler import (
    WorkflowAdapterRegistry,
    WorkflowScheduler,
)

_TERMINAL = {
    KnowledgeSyncStatus.COMPLETED,
    KnowledgeSyncStatus.FAILED,
    KnowledgeSyncStatus.CANCELLED,
}


class KnowledgeSyncScheduler:
    """Compatibility facade backed exclusively by the shared Workflow Kernel."""

    def __init__(
        self,
        *,
        application: ObsidianKnowledgeSync,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        workflow_scheduler: WorkflowScheduler | None = None,
        adapters: WorkflowAdapterRegistry | None = None,
        wait_timeout: float = 300.0,
        **_legacy_options,
    ) -> None:
        if wait_timeout <= 0:
            raise ValueError("Knowledge Sync wait timeout must be positive")
        if (workflow_scheduler is None) != (adapters is None):
            raise ValueError("Workflow Scheduler and Adapter Registry must be configured together")
        self._application = application
        self._unit_of_work_factory = unit_of_work_factory
        self._wait_timeout = wait_timeout
        self._owns_scheduler = workflow_scheduler is None
        selected_adapters = adapters or WorkflowAdapterRegistry()
        selected_adapters.register(
            KNOWLEDGE_SYNC_NODE_KIND,
            KnowledgeSyncWorkflowAdapter(
                application=application,
                unit_of_work_factory=unit_of_work_factory,
            ),
        )
        self._workflow_scheduler = workflow_scheduler or WorkflowScheduler(
            unit_of_work_factory=unit_of_work_factory,
            adapters=selected_adapters,
        )

    def close(self) -> None:
        if self._owns_scheduler:
            self._workflow_scheduler.close()

    def start(self, request: KnowledgeSyncStartInput) -> KnowledgeSyncRun:
        run = self._application.enqueue(request)
        if run.status not in _TERMINAL:
            with self._unit_of_work_factory() as unit_of_work:
                ensure_knowledge_workflow(unit_of_work, run)
                unit_of_work.commit()
            self._workflow_scheduler.wake()
        return run

    def run(self, request: KnowledgeSyncStartInput) -> KnowledgeSyncRun:
        run = self.start(request)
        if run.status in _TERMINAL:
            return run
        with self._unit_of_work_factory() as unit_of_work:
            workflow = unit_of_work.workflows.get_by_owner(
                owner_kind="knowledge_sync",
                owner_id=str(run.id),
                engine_version=1,
            )
        if workflow is None:
            raise RuntimeError("Knowledge Sync Workflow is unavailable")
        self._workflow_scheduler.wait(workflow.run.id, timeout=self._wait_timeout)
        return self.get(run.id)

    def get(self, run_id: UUID) -> KnowledgeSyncRun:
        return self._application.get(run_id)

    def cancel(self, run_id: UUID) -> KnowledgeSyncRun:
        with self._unit_of_work_factory() as unit_of_work:
            run = unit_of_work.knowledge.cancel_sync_run(run_id)
            workflow = unit_of_work.workflows.get_by_owner(
                owner_kind="knowledge_sync",
                owner_id=str(run.id),
                engine_version=1,
            )
            payload = {
                "run_id": str(run.id),
                "source_id": str(run.source_id),
                "status": run.status.value,
            }
            if (
                run.status is KnowledgeSyncStatus.CANCELLED
                and not unit_of_work.commands.has_domain_event(
                    event_type="knowledge.sync.cancelled",
                    project_id=run.project_id,
                    conversation_id=None,
                    payload=payload,
                )
            ):
                unit_of_work.commands.append_domain_event(
                    event_type="knowledge.sync.cancelled",
                    visibility=EventVisibility.USER,
                    message="Knowledge sync cancelled",
                    payload=payload,
                    actor="user",
                    project_id=run.project_id,
                )
            unit_of_work.commit()
        if workflow is not None:
            self._workflow_scheduler.cancel(workflow.run.id)
        return run

    def recover_interrupted(self) -> int:
        resumable_workflows = []
        with self._unit_of_work_factory() as unit_of_work:
            runs = unit_of_work.knowledge.resumable_sync_runs()
            for run in runs:
                workflow = ensure_knowledge_workflow(unit_of_work, run)
                if workflow.run.status is WorkflowRunStatus.PAUSED:
                    resumable_workflows.append(workflow.run.id)
            if runs:
                unit_of_work.commit()
        for workflow_run_id in resumable_workflows:
            self._workflow_scheduler.resume(workflow_run_id)
        if runs:
            self._workflow_scheduler.wake()
        return len(runs)


__all__ = ["KnowledgeSyncScheduler"]
