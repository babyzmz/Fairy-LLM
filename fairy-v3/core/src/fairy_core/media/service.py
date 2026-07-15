from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fairy_core.application.errors import ApprovalRequiredError
from fairy_core.commanding import CommandRun, CommandStatus
from fairy_core.commanding.bus import CommandBus, CommandRequest
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.commanding.settings import ExecutionPolicyResolver
from fairy_core.contracts.media import (
    MediaAudioGenerateInput,
    MediaImageGenerateInput,
    MediaVideoCancelInput,
    MediaVideoJobInput,
    MediaVideoStartInput,
)
from fairy_core.domain.errors import CapabilityUnavailableError
from fairy_core.domain.models import ScopeContract
from fairy_core.media.application import MediaApplication, MediaGenerationResult
from fairy_core.media.models import MediaGenerationJob, MediaGenerationStatus
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import CancellationToken


@dataclass(frozen=True, slots=True)
class _StartedCommand:
    run: CommandRun
    scope: ScopeContract
    replayed: bool


class MediaService:
    def __init__(
        self,
        *,
        application: MediaApplication,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        registry: ToolRegistry,
        execution_policy: ExecutionPolicyResolver,
        scope_resolver,
    ) -> None:
        self._application = application
        self._unit_of_work_factory = unit_of_work_factory
        self._registry = registry
        self._execution_policy = execution_policy
        self._scope_resolver = scope_resolver

    @property
    def handlers(self) -> dict[str, Any]:
        return {
            "media.images.generate": self.generate_image,
            "media.audio.generate": self.generate_music,
            "media.videos.start": self.start_video,
            "media.videos.get": self.get_video,
            "media.videos.cancel": self.cancel_video,
        }

    def generate_image(self, request: MediaImageGenerateInput) -> dict[str, object]:
        started = self._start_command(
            task_id=request.task_id,
            command_name="media.images.generate",
            payload={
                "prompt": request.prompt,
                "output_path": request.output_path,
                "size": request.size,
                "aspect_ratio": request.aspect_ratio,
                "seed": request.seed,
            },
            idempotency_key=request.idempotency_key,
            user_confirmed=request.user_confirmed,
        )
        if started.replayed:
            return _job_model(
                self._replayed_job(
                    idempotency_key=f"media-job:{request.idempotency_key}",
                    command_run_id=started.run.id,
                )
            )
        try:
            result = self._application.generate_image(
                task_id=request.task_id,
                command_run=started.run,
                prompt=request.prompt,
                output_path=request.output_path,
                size=request.size,
                aspect_ratio=request.aspect_ratio,
                seed=request.seed,
                idempotency_key=f"media-job:{request.idempotency_key}",
                cancellation=CancellationToken(),
            )
            self._complete(started.run.id, result)
            return _job_model(result.job)
        except BaseException as error:
            self._fail(started.run.id, error)
            raise

    def generate_music(self, request: MediaAudioGenerateInput) -> dict[str, object]:
        started = self._start_command(
            task_id=request.task_id,
            command_name="media.audio.generate",
            payload={
                "prompt": request.prompt,
                "output_path": request.output_path,
                "output_format": request.output_format,
                "seed": request.seed,
            },
            idempotency_key=request.idempotency_key,
            user_confirmed=request.user_confirmed,
        )
        if started.replayed:
            return _job_model(
                self._replayed_job(
                    idempotency_key=f"media-job:{request.idempotency_key}",
                    command_run_id=started.run.id,
                )
            )
        try:
            result = self._application.generate_music(
                task_id=request.task_id,
                command_run=started.run,
                prompt=request.prompt,
                output_path=request.output_path,
                output_format=request.output_format,
                seed=request.seed,
                idempotency_key=f"media-job:{request.idempotency_key}",
                cancellation=CancellationToken(),
            )
            self._complete(started.run.id, result)
            return _job_model(result.job)
        except BaseException as error:
            self._fail(started.run.id, error)
            raise

    def start_video(self, request: MediaVideoStartInput) -> dict[str, object]:
        started = self._start_command(
            task_id=request.task_id,
            command_name="media.videos.start",
            payload={
                "prompt": request.prompt,
                "output_path": request.output_path,
                "duration_seconds": request.duration_seconds,
                "resolution": request.resolution,
                "aspect_ratio": request.aspect_ratio,
                "generate_audio": request.generate_audio,
                "seed": request.seed,
            },
            idempotency_key=request.idempotency_key,
            user_confirmed=request.user_confirmed,
        )
        if started.replayed:
            return _job_model(
                self._replayed_job(
                    idempotency_key=f"media-job:{request.idempotency_key}",
                    command_run_id=started.run.id,
                )
            )
        try:
            result = self._application.start_video(
                task_id=request.task_id,
                command_run=started.run,
                prompt=request.prompt,
                output_path=request.output_path,
                duration_seconds=request.duration_seconds,
                resolution=request.resolution,
                aspect_ratio=request.aspect_ratio,
                generate_audio=request.generate_audio,
                seed=request.seed,
                idempotency_key=f"media-job:{request.idempotency_key}",
                cancellation=CancellationToken(),
            )
            self._complete(started.run.id, result)
            return _job_model(result.job)
        except BaseException as error:
            self._fail(started.run.id, error)
            raise

    def get_video(self, request: MediaVideoJobInput) -> dict[str, object]:
        with self._unit_of_work_factory() as unit_of_work:
            job = unit_of_work.state.get_media_job(request.job_id)
        if job is None:
            raise KeyError(f"Media generation job not found: {request.job_id}")
        if job.status in {
            MediaGenerationStatus.COMPLETED,
            MediaGenerationStatus.FAILED,
            MediaGenerationStatus.CANCELLED,
            MediaGenerationStatus.INTERRUPTED,
        }:
            return _job_model(job)
        started = self._start_command(
            task_id=job.task_id,
            command_name="media.videos.poll",
            payload={"job_id": str(job.id), "expected_revision": job.revision},
            idempotency_key=f"media-video-poll:{job.id}:{job.revision}",
            user_confirmed=True,
        )
        if started.replayed:
            with self._unit_of_work_factory() as unit_of_work:
                replayed = unit_of_work.state.get_media_job(job.id)
            if replayed is None:
                raise RuntimeError("Replayed media poll lost its durable Job")
            return _job_model(replayed)
        try:
            result = self._application.poll_video(
                job_id=job.id,
                command_run=started.run,
                cancellation=CancellationToken(),
            )
            self._complete(started.run.id, result)
            return _job_model(result.job)
        except BaseException as error:
            self._fail(started.run.id, error)
            raise

    def cancel_video(self, request: MediaVideoCancelInput) -> dict[str, object]:
        with self._unit_of_work_factory() as unit_of_work:
            job = unit_of_work.state.get_media_job(request.job_id)
        if job is None:
            raise KeyError(f"Media generation job not found: {request.job_id}")
        started = self._start_command(
            task_id=job.task_id,
            command_name="media.videos.cancel",
            payload={
                "job_id": str(job.id),
                "expected_revision": request.expected_revision,
            },
            idempotency_key=request.idempotency_key,
            user_confirmed=request.user_confirmed,
        )
        if started.replayed:
            with self._unit_of_work_factory() as unit_of_work:
                replayed = unit_of_work.state.get_media_job(job.id)
            if replayed is None:
                raise RuntimeError("Replayed media cancellation lost its durable Job")
            return _job_model(replayed)
        try:
            result = self._application.cancel_video(
                job_id=job.id,
                expected_revision=request.expected_revision,
                command_run=started.run,
            )
            self._complete(started.run.id, result)
            return _job_model(result.job)
        except BaseException as error:
            self._fail(started.run.id, error)
            raise

    def _start_command(
        self,
        *,
        task_id: UUID,
        command_name: str,
        payload: dict[str, object],
        idempotency_key: str,
        user_confirmed: bool,
    ) -> _StartedCommand:
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            scope = self._scope_resolver(unit_of_work.state, task)
            execution = self._execution_policy.resolve(
                unit_of_work.execution_settings,
                execution_target=scope.execution_target,
            )
            bus = self._bus(unit_of_work.commands)
            dispatch = bus.submit(
                CommandRequest(
                    tool_name=command_name,
                    actor="user",
                    scope=scope,
                    payload=payload,
                    idempotency_key=idempotency_key,
                ),
                profile=execution.profile,
                capability_overrides=dict(execution.capability_overrides),
                sandbox_healthy=execution.sandbox_healthy,
            )
            if not dispatch.accepted or dispatch.run is None:
                raise RuntimeError(dispatch.error_code or "Media command was rejected")
            run = dispatch.run
            if dispatch.requires_approval:
                if not user_confirmed:
                    raise ApprovalRequiredError("Media generation requires explicit confirmation")
                run = bus.decide_approval(run.id, approved=True)
            if run.status is CommandStatus.QUEUED:
                run = bus.start(run.id)
                replayed = False
            elif run.status is CommandStatus.SUCCEEDED:
                replayed = True
            elif run.status is not CommandStatus.RUNNING:
                raise RuntimeError(f"Media command cannot resume from {run.status.value}")
            else:
                replayed = False
            unit_of_work.commit()
        return _StartedCommand(run=run, scope=scope, replayed=replayed)

    def _replayed_job(
        self,
        *,
        idempotency_key: str,
        command_run_id: UUID,
    ) -> MediaGenerationJob:
        with self._unit_of_work_factory() as unit_of_work:
            job = unit_of_work.state.find_media_job_by_idempotency_key(idempotency_key)
        if job is None or job.command_run_id != command_run_id:
            raise RuntimeError("Replayed media command has no matching durable Job")
        return job

    def _complete(self, run_id: UUID, result: MediaGenerationResult) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            run = unit_of_work.commands.get_run(run_id)
            if run is None:
                raise RuntimeError("Media CommandRun disappeared")
            if run.status is CommandStatus.SUCCEEDED:
                return
            if run.status is not CommandStatus.RUNNING:
                raise RuntimeError("Media CommandRun is no longer active")
            self._bus(unit_of_work.commands).complete(
                run.id,
                output={
                    "job_id": str(result.job.id),
                    "status": result.job.status.value,
                    "artifact_id": (
                        str(result.artifact.id) if result.artifact is not None else None
                    ),
                },
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
            )
            unit_of_work.commit()

    def _fail(self, run_id: UUID, error: BaseException) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            run = unit_of_work.commands.get_run(run_id)
            if run is None or run.status is not CommandStatus.RUNNING:
                return
            code = getattr(error, "error_code", None)
            error_code = (
                code if isinstance(code, str) and code and len(code) <= 128 else "MEDIA_FAILED"
            )
            self._bus(unit_of_work.commands).fail(
                run.id,
                error_code=error_code,
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
            )
            unit_of_work.commit()

    def _bus(self, ledger) -> CommandBus:
        return CommandBus(
            registry=self._registry,
            policy=PolicyEngine(self._registry),
            ledger=ledger,
        )


class UnavailableMediaService:
    @property
    def handlers(self) -> dict[str, Any]:
        return {
            "media.images.generate": self._unavailable,
            "media.audio.generate": self._unavailable,
            "media.videos.start": self._unavailable,
            "media.videos.get": self._unavailable,
            "media.videos.cancel": self._unavailable,
        }

    @staticmethod
    def _unavailable(_request: Any) -> None:
        raise CapabilityUnavailableError("Media generation is not configured")


def _job_model(job: MediaGenerationJob) -> dict[str, object]:
    return {
        "id": job.id,
        "project_id": job.project_id,
        "workspace_id": job.workspace_id,
        "conversation_id": job.conversation_id,
        "task_id": job.task_id,
        "version_id": job.version_id,
        "turn_id": job.turn_id,
        "kind": job.kind,
        "model_id": job.model_id,
        "endpoint_kind": job.endpoint_kind,
        "output_path": job.output_path,
        "status": job.status,
        "progress": job.progress,
        "artifact_ids": (
            (job.artifact_id,) if job.status is MediaGenerationStatus.COMPLETED else ()
        ),
        "usage_cost": job.usage_cost,
        "error_code": job.error_code,
        "revision": job.revision,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


__all__ = ["MediaService", "UnavailableMediaService"]
