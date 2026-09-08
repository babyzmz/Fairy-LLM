from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import RLock
from uuid import UUID

from fairy_core.assistant.tools import uses_deferred_media
from fairy_core.commanding import CommandRun, CommandStatus
from fairy_core.commanding.bus import CommandBus
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.contracts.common import ExecutionTarget
from fairy_core.domain.errors import InvalidTransitionError, WorkerFenceError
from fairy_core.domain.ids import new_id
from fairy_core.media.application import MediaApplication, MediaGenerationResult
from fairy_core.media.models import MediaGenerationJob, MediaGenerationKind
from fairy_core.media.work_queue import MEDIA_COMMAND_LEASE_DURATION
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import CancellationToken, ProviderCancelledError
from fairy_core.workflow.models import (
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
    WorkflowTriggerKind,
)
from fairy_core.workflow.scheduler import (
    WorkflowCancelled,
    WorkflowNodeResult,
    WorkflowRetryableError,
)

MEDIA_WORKFLOW_ENGINE_VERSION = 1
MEDIA_SUBMIT_NODE_KIND = "media.generation.submit"
MEDIA_WAIT_NODE_KIND = "media.generation.wait"
MEDIA_POLL_NODE_KIND = "media.generation.poll"
MEDIA_ARCHIVE_NODE_KIND = "media.generation.archive"
MEDIA_GENERATION_NODE_KIND = MEDIA_SUBMIT_NODE_KIND
MEDIA_GENERATION_NODE_KINDS = (
    MEDIA_SUBMIT_NODE_KIND,
    MEDIA_WAIT_NODE_KIND,
    MEDIA_POLL_NODE_KIND,
    MEDIA_ARCHIVE_NODE_KIND,
)


class MediaWorkflowError(RuntimeError):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


def ensure_media_workflow(unit_of_work, job: MediaGenerationJob):
    existing = unit_of_work.workflows.get_by_owner(
        owner_kind="media_generation",
        owner_id=str(job.id),
        engine_version=MEDIA_WORKFLOW_ENGINE_VERSION,
    )
    if existing is not None:
        return existing
    task = unit_of_work.state.get_task(job.task_id)
    if task is None:
        raise KeyError(f"Task not found: {job.task_id}")
    parent_run_id = None
    if job.turn_id is not None:
        turn = unit_of_work.assistant.get_turn(job.turn_id)
        parent_run_id = turn.workflow_run_id if turn is not None else None
    workflow = WorkflowRun.create(
        owner_kind="media_generation",
        owner_id=str(job.id),
        execution_target=ExecutionTarget(task.execution_target),
        trigger_kind=WorkflowTriggerKind.DOMAIN,
        idempotency_key=f"media-generation:{job.idempotency_key}",
        conversation_id=job.conversation_id,
        task_id=job.task_id,
        project_id=job.project_id,
        parent_run_id=parent_run_id,
        engine_version=MEDIA_WORKFLOW_ENGINE_VERSION,
    )
    resource_keys = (
        f"media-job:{job.id}",
        f"workspace-version:{job.version_id}",
    )
    submit = WorkflowNode.create(
        run_id=workflow.id,
        plan_revision=1,
        node_key="submit",
        kind=MEDIA_SUBMIT_NODE_KIND,
        payload={"job_id": str(job.id)},
        public_summary=f"Submitting {job.kind.value} generation",
        ready=True,
        resource_keys=resource_keys,
        max_attempts=3,
    )
    wait = WorkflowNode.create(
        run_id=workflow.id,
        plan_revision=1,
        node_key="wait",
        kind=MEDIA_WAIT_NODE_KIND,
        payload={"job_id": str(job.id)},
        public_summary="Waiting for the Media Provider",
        resource_keys=resource_keys,
        max_attempts=3,
    )
    poll = WorkflowNode.create(
        run_id=workflow.id,
        plan_revision=1,
        node_key="poll",
        kind=MEDIA_POLL_NODE_KIND,
        payload={"job_id": str(job.id)},
        public_summary=f"Polling {job.kind.value} generation",
        resource_keys=resource_keys,
        max_attempts=10_000 if job.kind is MediaGenerationKind.VIDEO else 3,
    )
    archive = WorkflowNode.create(
        run_id=workflow.id,
        plan_revision=1,
        node_key="archive",
        kind=MEDIA_ARCHIVE_NODE_KIND,
        payload={"job_id": str(job.id)},
        public_summary="Archiving generated Media",
        resource_keys=resource_keys,
        max_attempts=3,
    )
    nodes = (submit, wait, poll, archive)
    edges = (
        WorkflowEdge(workflow.id, 1, submit.id, wait.id),
        WorkflowEdge(workflow.id, 1, wait.id, poll.id),
        WorkflowEdge(workflow.id, 1, poll.id, archive.id),
    )
    return unit_of_work.workflows.create(workflow, nodes=nodes, edges=edges)


class MediaGenerationWorkflowAdapter:
    def __init__(
        self,
        *,
        application: MediaApplication,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        registry: ToolRegistry,
        video_poll_interval: float = 1.0,
    ) -> None:
        if video_poll_interval <= 0:
            raise ValueError("Media video poll interval must be positive")
        self._application = application
        self._unit_of_work_factory = unit_of_work_factory
        self._registry = registry
        self._video_poll_interval = video_poll_interval
        self._worker_id = f"media-workflow:{new_id()}"
        self._commands: dict[UUID, CommandRun] = {}
        self._lock = RLock()

    def heartbeat(self, node: WorkflowNode) -> bool:
        with self._lock:
            command = self._commands.get(node.id)
        if command is None:
            return True
        if command.lease_owner is None:
            return False
        with self._unit_of_work_factory() as unit_of_work:
            renewed = unit_of_work.commands.renew(
                command.id,
                lease_owner=command.lease_owner,
                lease_fence=command.lease_fence,
                lease_until=datetime.now(UTC) + MEDIA_COMMAND_LEASE_DURATION,
            )
            if renewed:
                unit_of_work.commit()
            return renewed

    def execute(
        self,
        node: WorkflowNode,
        cancellation: CancellationToken,
    ) -> WorkflowNodeResult:
        if node.kind not in MEDIA_GENERATION_NODE_KINDS or set(node.payload) != {"job_id"}:
            raise MediaWorkflowError("MEDIA_WORKFLOW_PAYLOAD_INVALID")
        try:
            job_id = UUID(str(node.payload["job_id"]))
        except (TypeError, ValueError) as error:
            raise MediaWorkflowError("MEDIA_WORKFLOW_PAYLOAD_INVALID") from error
        if node.kind == MEDIA_WAIT_NODE_KIND:
            return self._wait(job_id, node)
        if node.kind == MEDIA_ARCHIVE_NODE_KIND:
            return self._archive(job_id)
        return self._execute_provider(job_id, node, cancellation)

    def _execute_provider(
        self,
        job_id: UUID,
        node: WorkflowNode,
        cancellation: CancellationToken,
    ) -> WorkflowNodeResult:
        command = self._claim_user_command(job_id)
        if command is not None:
            with self._lock:
                self._commands[node.id] = command
        try:
            result = self._application.execute_job(
                job_id,
                cancellation=cancellation,
                attempt_number=node.attempt_count,
            )
            if result.job.is_terminal:
                self._settle_command(command, result=result, error=None)
                return self._phase_result(result, phase=node.kind)
            if result.job.kind is not MediaGenerationKind.VIDEO:
                raise MediaWorkflowError("MEDIA_WORKFLOW_UNSETTLED")
            self._settle_command(command, result=result, error=None)
            if node.kind == MEDIA_SUBMIT_NODE_KIND:
                return WorkflowNodeResult(
                    output={
                        "job_id": str(result.job.id),
                        "status": result.job.status.value,
                        "progress": result.job.progress,
                    },
                    public_summary="Video generation submitted",
                )
            return WorkflowNodeResult(
                output={
                    "job_id": str(result.job.id),
                    "status": result.job.status.value,
                    "progress": result.job.progress,
                },
                public_summary=f"Video generation is {result.job.progress}% complete",
                available_at=datetime.now(UTC) + timedelta(seconds=self._video_poll_interval),
            )
        except ProviderCancelledError as error:
            if cancellation.is_interrupted:
                self._abandon_command(command)
            else:
                self._settle_command(command, result=None, error=error)
            raise
        except Exception as error:
            result = self._application.get_result(
                job_id,
                include_active_video=False,
                raise_terminal_error=False,
            )
            if result is None:
                self._abandon_command(command)
                raise WorkflowRetryableError(
                    "Media generation will be retried",
                    error_code=_safe_error_code(error),
                ) from error
            self._settle_command(command, result=None, error=error)
            if result.job.status.value == "cancelled":
                raise WorkflowCancelled from error
            raise MediaWorkflowError(_safe_error_code(error)) from error
        finally:
            with self._lock:
                self._commands.pop(node.id, None)

    def _wait(self, job_id: UUID, node: WorkflowNode) -> WorkflowNodeResult:
        result = self._application.get_result(
            job_id,
            include_active_video=False,
            raise_terminal_error=False,
        )
        if result is not None:
            return self._phase_result(result, phase=node.kind)
        if node.attempt_count == 1:
            return WorkflowNodeResult(
                output={"job_id": str(job_id), "status": "waiting"},
                public_summary="Waiting without occupying a Workflow Worker",
                available_at=datetime.now(UTC) + timedelta(seconds=self._video_poll_interval),
            )
        return WorkflowNodeResult(
            output={"job_id": str(job_id), "status": "ready_to_poll"},
            public_summary="Media generation is ready to poll",
        )

    def _archive(self, job_id: UUID) -> WorkflowNodeResult:
        result = self._application.get_result(
            job_id,
            include_active_video=False,
            raise_terminal_error=False,
        )
        if result is None:
            raise WorkflowRetryableError(
                "Media result is not ready to archive",
                error_code="MEDIA_ARCHIVE_NOT_READY",
            )
        return self._result(result)

    def _claim_user_command(self, job_id: UUID) -> CommandRun | None:
        with self._unit_of_work_factory() as unit_of_work:
            job = unit_of_work.state.get_media_job(job_id)
            if job is None:
                raise KeyError(f"Media generation job not found: {job_id}")
            run = unit_of_work.commands.get_run(job.command_run_id)
            if run is None:
                raise MediaWorkflowError("MEDIA_COMMAND_MISSING")
            if (
                (run.actor == "assistant" and not uses_deferred_media(run))
                or run.status is CommandStatus.SUCCEEDED
            ):
                return None
            if run.status not in {CommandStatus.QUEUED, CommandStatus.RUNNING}:
                raise MediaWorkflowError("MEDIA_COMMAND_INACTIVE")
            try:
                claimed = unit_of_work.commands.claim(
                    run.id,
                    worker_id=self._worker_id,
                    lease_until=datetime.now(UTC) + MEDIA_COMMAND_LEASE_DURATION,
                )
            except (InvalidTransitionError, WorkerFenceError) as error:
                raise WorkflowRetryableError(
                    "Media Command is currently leased",
                    error_code="MEDIA_COMMAND_BUSY",
                ) from error
            unit_of_work.commit()
            return claimed

    def _settle_command(
        self,
        command: CommandRun | None,
        *,
        result: MediaGenerationResult | None,
        error: BaseException | None,
    ) -> None:
        if command is None:
            return
        with self._unit_of_work_factory() as unit_of_work:
            if uses_deferred_media(command):
                # The Assistant records Invocation + Command result in one transaction.
                # Media owns the lease only while its Provider operation is active.
                if command.lease_owner is None or not unit_of_work.commands.abandon(
                    command.id, lease_owner=command.lease_owner, lease_fence=command.lease_fence,
                ):
                    raise WorkerFenceError("Media completion lost its Command lease")
                unit_of_work.commit()
                return
            persisted = unit_of_work.commands.get_run(command.id)
            if persisted is None or persisted.status is not CommandStatus.RUNNING:
                return
            bus = CommandBus(
                registry=self._registry,
                policy=PolicyEngine(self._registry),
                ledger=unit_of_work.commands,
            )
            if result is not None and error is None:
                bus.complete(
                    persisted.id,
                    output={
                        "job_id": str(result.job.id),
                        "status": result.job.status.value,
                        "artifact_id": (
                            str(result.artifact.id) if result.artifact is not None else None
                        ),
                    },
                    lease_owner=command.lease_owner,
                    lease_fence=command.lease_fence,
                )
            else:
                bus.fail(
                    persisted.id,
                    error_code=_safe_error_code(error or RuntimeError("Media failed")),
                    lease_owner=command.lease_owner,
                    lease_fence=command.lease_fence,
                )
            unit_of_work.commit()

    def _abandon_command(self, command: CommandRun | None) -> None:
        if command is None or command.lease_owner is None:
            return
        with self._unit_of_work_factory() as unit_of_work:
            if unit_of_work.commands.abandon(
                command.id,
                lease_owner=command.lease_owner,
                lease_fence=command.lease_fence,
            ):
                unit_of_work.commit()

    @staticmethod
    def _phase_result(
        result: MediaGenerationResult,
        *,
        phase: str,
    ) -> WorkflowNodeResult:
        job = result.job
        if job.status.value == "cancelled":
            raise WorkflowCancelled
        if job.status.value != "completed" or result.artifact is None:
            raise MediaWorkflowError(job.error_code or "MEDIA_FAILED")
        return WorkflowNodeResult(
            output={
                "job_id": str(job.id),
                "status": job.status.value,
                "artifact_id": str(result.artifact.id),
                "phase": phase,
            },
            public_summary=f"Generated {job.kind.value}",
        )

    @staticmethod
    def _result(result: MediaGenerationResult) -> WorkflowNodeResult:
        job = result.job
        if job.status.value == "cancelled":
            raise WorkflowCancelled
        if job.status.value != "completed" or result.artifact is None:
            raise MediaWorkflowError(job.error_code or "MEDIA_FAILED")
        return WorkflowNodeResult(
            output={
                "job_id": str(job.id),
                "status": job.status.value,
                "artifact_id": str(result.artifact.id),
                "output_path": job.output_path,
            },
            evidence_refs=(f"artifact:{result.artifact.id}",),
            public_summary=f"Generated {job.kind.value} at {job.output_path}",
        )


def _safe_error_code(error: BaseException) -> str:
    value = getattr(error, "error_code", None)
    if isinstance(value, str) and value.strip():
        return value.strip()[:128]
    if isinstance(error, TimeoutError):
        return "MEDIA_TIMEOUT"
    return "MEDIA_FAILED"


__all__ = [
    "MEDIA_ARCHIVE_NODE_KIND",
    "MEDIA_GENERATION_NODE_KIND",
    "MEDIA_GENERATION_NODE_KINDS",
    "MEDIA_POLL_NODE_KIND",
    "MEDIA_SUBMIT_NODE_KIND",
    "MEDIA_WAIT_NODE_KIND",
    "MEDIA_WORKFLOW_ENGINE_VERSION",
    "MediaGenerationWorkflowAdapter",
    "MediaWorkflowError",
    "ensure_media_workflow",
]
