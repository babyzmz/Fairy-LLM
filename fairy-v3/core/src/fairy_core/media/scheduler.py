from __future__ import annotations

import time
from uuid import UUID

from fairy_core.commanding.registry import ToolRegistry
from fairy_core.media.application import MediaApplication, MediaGenerationResult
from fairy_core.media.workflow import (
    MEDIA_GENERATION_NODE_KINDS,
    MediaGenerationWorkflowAdapter,
    ensure_media_workflow,
)
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import CancellationToken, ProviderCancelledError
from fairy_core.workflow.models import WorkflowRunStatus
from fairy_core.workflow.scheduler import WorkflowAdapterRegistry, WorkflowScheduler


class MediaScheduler:
    """Media compatibility facade backed exclusively by the shared Workflow Kernel."""

    def __init__(
        self,
        *,
        application: MediaApplication,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        registry: ToolRegistry,
        workflow_scheduler: WorkflowScheduler | None = None,
        adapters: WorkflowAdapterRegistry | None = None,
        video_poll_interval: float = 1.0,
        wait_timeout: float = 900.0,
        **_legacy_options,
    ) -> None:
        if min(video_poll_interval, wait_timeout) <= 0:
            raise ValueError("Media Workflow intervals must be positive")
        if (workflow_scheduler is None) != (adapters is None):
            raise ValueError("Workflow Scheduler and Adapter Registry must be configured together")
        self._application = application
        self._unit_of_work_factory = unit_of_work_factory
        self._wait_timeout = wait_timeout
        self._owns_scheduler = workflow_scheduler is None
        selected_adapters = adapters or WorkflowAdapterRegistry()
        adapter = MediaGenerationWorkflowAdapter(
            application=application,
            unit_of_work_factory=unit_of_work_factory,
            registry=registry,
            video_poll_interval=video_poll_interval,
        )
        for kind in MEDIA_GENERATION_NODE_KINDS:
            selected_adapters.register(kind, adapter)
        self._workflow_scheduler = workflow_scheduler or WorkflowScheduler(
            unit_of_work_factory=unit_of_work_factory,
            adapters=selected_adapters,
        )

    def close(self) -> None:
        if self._owns_scheduler:
            self._workflow_scheduler.close()

    def enqueue(self, job_id: UUID) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            job = unit_of_work.state.get_media_job(job_id)
            if job is None:
                raise KeyError(f"Media generation job not found: {job_id}")
            if not job.is_terminal:
                ensure_media_workflow(unit_of_work, job)
                unit_of_work.commit()
        self._workflow_scheduler.wake()

    def run(
        self,
        job_id: UUID,
        *,
        cancellation: CancellationToken,
        return_when_video_active: bool = False,
    ) -> MediaGenerationResult:
        self.enqueue(job_id)
        workflow_run_id = self._workflow_run_id(job_id)
        deadline = time.monotonic() + self._wait_timeout
        while True:
            result = self._application.get_result(
                job_id,
                include_active_video=return_when_video_active,
            )
            if result is not None and not result.job.is_terminal:
                # An explicitly requested active video receipt is not completion.
                return result
            with self._unit_of_work_factory() as unit_of_work:
                workflow = unit_of_work.workflows.get(workflow_run_id)
            if workflow is None:
                raise RuntimeError("Media generation Workflow is unavailable")
            if workflow.run.status is WorkflowRunStatus.PAUSED:
                cancellation.interrupt()
                cancellation.raise_if_cancelled()
            if workflow.run.status is WorkflowRunStatus.CANCELLED:
                cancellation.cancel()
                cancellation.raise_if_cancelled()
            if workflow.run.status is WorkflowRunStatus.FAILED:
                raise RuntimeError(workflow.run.error_code or "MEDIA_WORKFLOW_FAILED")
            if workflow.run.status is WorkflowRunStatus.COMPLETED:
                # The provider may persist its artifact in submit, before the
                # remaining wait/poll/archive nodes settle. Do not expose a
                # synchronous completed reply while its durable Run is active.
                completed = result or self._application.get_result(job_id)
                if completed is not None:
                    return completed
                raise RuntimeError("Media Workflow completed without a durable result")
            try:
                cancellation.raise_if_cancelled()
            except ProviderCancelledError:
                if not cancellation.is_interrupted:
                    self.cancel(job_id)
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Media generation did not settle before its wait deadline")
            time.sleep(min(0.05, remaining))

    def _workflow_run_id(self, job_id: UUID) -> UUID:
        with self._unit_of_work_factory() as unit_of_work:
            workflow = unit_of_work.workflows.get_by_owner(
                owner_kind="media_generation",
                owner_id=str(job_id),
                engine_version=1,
            )
        if workflow is None:
            raise RuntimeError("Media generation Workflow is unavailable")
        return workflow.run.id

    def cancel(self, job_id: UUID) -> bool:
        job = self._application.cancel_work(job_id)
        with self._unit_of_work_factory() as unit_of_work:
            workflow = unit_of_work.workflows.get_by_owner(
                owner_kind="media_generation",
                owner_id=str(job_id),
                engine_version=1,
            )
        if workflow is not None:
            self._workflow_scheduler.cancel(workflow.run.id)
        return workflow is not None or job.is_terminal

    def recover_interrupted(self) -> dict[str, int]:
        result = self._application.recover_interrupted()
        resumable_workflows = []
        with self._unit_of_work_factory() as unit_of_work:
            for job in unit_of_work.state.recoverable_media_jobs():
                workflow = ensure_media_workflow(unit_of_work, job)
                if (
                    workflow.run.pause_requested
                    and workflow.run.status is not WorkflowRunStatus.PAUSED
                ):
                    # Older shutdown races left running/queued + pause_requested
                    # after the last Attempt had already yielded. Reconcile from
                    # persisted Attempts, not from a stale in-memory Run status.
                    workflow = unit_of_work.workflows.request_pause(workflow.run.id)
                if workflow.run.status is WorkflowRunStatus.PAUSED:
                    resumable_workflows.append(workflow.run.id)
            unit_of_work.commit()
        for run_id in resumable_workflows:
            self._workflow_scheduler.resume(run_id)
        self._workflow_scheduler.wake()
        return result


__all__ = ["MediaScheduler"]
