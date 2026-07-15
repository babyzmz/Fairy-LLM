from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from fairy_core.assistant.models import ToolInvocation
from fairy_core.assistant.provider_attempts import ProviderAttemptRecorder
from fairy_core.commanding import CommandRun, CommandStatus, EventVisibility
from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.domain.execution import Artifact, ArtifactType, ArtifactVisibility
from fairy_core.domain.models import ScopeContract
from fairy_core.media.models import (
    MediaGenerationJob,
    MediaGenerationKind,
    MediaGenerationStatus,
)
from fairy_core.media.ports import (
    GeneratedMedia,
    ImageGenerationRequest,
    MediaProvider,
    MediaProviderVideoStatus,
    MusicGenerationRequest,
    VideoGenerationRequest,
    VideoProviderJob,
)
from fairy_core.media.staging import MediaStagingStore
from fairy_core.model_catalog.models import (
    ModelAvailability,
    ModelEndpointKind,
)
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import (
    CancellationToken,
    ModelExecutionRole,
    ProviderAttemptEvent,
    ProviderAttemptStatus,
    ProviderAuthenticationError,
    ProviderCancelledError,
    ProviderContentRejectedError,
    ProviderError,
    ProviderErrorCategory,
    ProviderNetworkError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from fairy_core.workspace.index import ProjectIndexer
from fairy_core.workspace.object_store import DEFAULT_MAX_FILE_BYTES, AssetMutation
from fairy_core.workspace.ports import WorkspaceProvisioner

IMAGE_MODEL_ID = "google/gemini-3.1-flash-lite-image"
MUSIC_MODEL_ID = "google/lyria-3-pro-preview"
VIDEO_MODEL_ID = "bytedance/seedance-2.0"

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class MediaGenerationResult:
    job: MediaGenerationJob
    artifact: Artifact | None


@dataclass(frozen=True, slots=True)
class _PreparedJob:
    job: MediaGenerationJob
    scope: ScopeContract
    run: CommandRun
    invocation: ToolInvocation | None
    zero_data_retention: bool
    max_workspace_bytes: int


class MediaApplication:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        scope_resolver,
        provider: MediaProvider,
        workspaces: WorkspaceProvisioner,
        staging: MediaStagingStore,
        indexer: ProjectIndexer | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._scope_resolver = scope_resolver
        self._provider = provider
        self._workspaces = workspaces
        self._staging = staging
        self._indexer = indexer or ProjectIndexer()
        self._provider_attempts = ProviderAttemptRecorder(unit_of_work_factory)

    def generate_image(
        self,
        *,
        task_id: UUID,
        command_run: CommandRun,
        prompt: str,
        output_path: str | None,
        size: str,
        aspect_ratio: str,
        seed: int | None,
        idempotency_key: str,
        cancellation: CancellationToken,
    ) -> MediaGenerationResult:
        spec = {
            "prompt": _prompt(prompt),
            "size": _choice(size, {"1024x1024"}, "image size"),
            "aspect_ratio": _choice(
                aspect_ratio,
                {"1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16"},
                "image aspect ratio",
            ),
            "seed": _seed(seed),
        }
        prepared = self._prepare_job(
            task_id=task_id,
            command_run=command_run,
            command_name="media.images.generate",
            kind=MediaGenerationKind.IMAGE,
            model_id=IMAGE_MODEL_ID,
            endpoint_kind=ModelEndpointKind.IMAGES,
            output_path=output_path or _default_output_path("image", idempotency_key, ".png"),
            request_spec=spec,
            idempotency_key=idempotency_key,
        )
        replay = self._terminal_result(prepared.job)
        if replay is not None:
            return replay
        self._begin(prepared)
        try:
            generated = self._provider_call(
                prepared,
                lambda: self._provider.generate_image(
                    ImageGenerationRequest(
                        model_id=IMAGE_MODEL_ID,
                        prompt=spec["prompt"],
                        size=spec["size"],
                        aspect_ratio=spec["aspect_ratio"],
                        seed=spec["seed"],
                        idempotency_key=idempotency_key,
                        zero_data_retention=prepared.zero_data_retention,
                    ),
                    cancellation,
                ),
            )
            return self._persist_generated(prepared, generated)
        except BaseException as error:
            self._record_failure(prepared.job.id, prepared.run.id, error)
            raise

    def generate_music(
        self,
        *,
        task_id: UUID,
        command_run: CommandRun,
        prompt: str,
        output_path: str | None,
        output_format: str,
        seed: int | None,
        idempotency_key: str,
        cancellation: CancellationToken,
    ) -> MediaGenerationResult:
        spec = {
            "prompt": _prompt(prompt),
            "output_format": _choice(output_format, {"wav"}, "music format"),
            "seed": _seed(seed),
        }
        prepared = self._prepare_job(
            task_id=task_id,
            command_run=command_run,
            command_name="media.audio.generate",
            kind=MediaGenerationKind.MUSIC,
            model_id=MUSIC_MODEL_ID,
            endpoint_kind=ModelEndpointKind.AUDIO,
            output_path=output_path or _default_output_path("music", idempotency_key, ".wav"),
            request_spec=spec,
            idempotency_key=idempotency_key,
        )
        replay = self._terminal_result(prepared.job)
        if replay is not None:
            return replay
        self._begin(prepared)
        try:
            generated = self._provider_call(
                prepared,
                lambda: self._provider.generate_music(
                    MusicGenerationRequest(
                        model_id=MUSIC_MODEL_ID,
                        prompt=spec["prompt"],
                        output_format=spec["output_format"],
                        seed=spec["seed"],
                        idempotency_key=idempotency_key,
                        zero_data_retention=prepared.zero_data_retention,
                    ),
                    cancellation,
                ),
            )
            return self._persist_generated(prepared, generated)
        except BaseException as error:
            self._record_failure(prepared.job.id, prepared.run.id, error)
            raise

    def start_video(
        self,
        *,
        task_id: UUID,
        command_run: CommandRun,
        prompt: str,
        output_path: str | None,
        duration_seconds: int,
        resolution: str,
        aspect_ratio: str,
        generate_audio: bool,
        seed: int | None,
        idempotency_key: str,
        cancellation: CancellationToken,
    ) -> MediaGenerationResult:
        spec = {
            "prompt": _prompt(prompt),
            "duration_seconds": _integer_range(duration_seconds, 1, 20, "video duration"),
            "resolution": _choice(resolution, {"720p", "1080p"}, "video resolution"),
            "aspect_ratio": _choice(
                aspect_ratio,
                {"1:1", "16:9", "9:16"},
                "video aspect ratio",
            ),
            "generate_audio": _boolean(generate_audio, "generate_audio"),
            "seed": _seed(seed),
        }
        prepared = self._prepare_job(
            task_id=task_id,
            command_run=command_run,
            command_name="media.videos.start",
            kind=MediaGenerationKind.VIDEO,
            model_id=VIDEO_MODEL_ID,
            endpoint_kind=ModelEndpointKind.VIDEOS,
            output_path=output_path or _default_output_path("video", idempotency_key, ".mp4"),
            request_spec=spec,
            idempotency_key=idempotency_key,
        )
        replay = self._terminal_result(prepared.job, include_active_video=True)
        if replay is not None:
            return replay
        if prepared.zero_data_retention:
            error = ProviderUnavailableError("OpenRouter video generation does not support ZDR")
            self._record_failure(prepared.job.id, prepared.run.id, error)
            raise error
        self._begin(prepared)
        try:
            provider_job = self._provider_call(
                prepared,
                lambda: self._provider.start_video(
                    VideoGenerationRequest(
                        model_id=VIDEO_MODEL_ID,
                        prompt=spec["prompt"],
                        duration_seconds=spec["duration_seconds"],
                        resolution=spec["resolution"],
                        aspect_ratio=spec["aspect_ratio"],
                        generate_audio=spec["generate_audio"],
                        seed=spec["seed"],
                        idempotency_key=idempotency_key,
                        zero_data_retention=False,
                    ),
                    cancellation,
                ),
            )
            job = self._bind_video(prepared, provider_job)
            return MediaGenerationResult(job=job, artifact=None)
        except BaseException as error:
            self._record_failure(prepared.job.id, prepared.run.id, error)
            raise

    def poll_video(
        self,
        *,
        job_id: UUID,
        command_run: CommandRun,
        cancellation: CancellationToken,
    ) -> MediaGenerationResult:
        prepared = self._prepare_existing(
            job_id=job_id,
            command_run=command_run,
            command_name="media.videos.poll",
        )
        replay = self._terminal_result(prepared.job, include_active_video=False)
        if replay is not None:
            return replay
        if prepared.job.provider_job_id is None:
            raise RuntimeError("video job has no provider identity")
        try:
            provider_job = self._provider_call(
                prepared,
                lambda: self._provider.get_video(
                    prepared.job.provider_job_id or "",
                    cancellation,
                ),
            )
            if provider_job.status is MediaProviderVideoStatus.COMPLETED:
                generated = self._provider_call(
                    prepared,
                    lambda: self._provider.download_video(
                        prepared.job.provider_job_id or "",
                        cancellation,
                    ),
                )
                if generated.usage_cost is None and provider_job.usage_cost is not None:
                    generated = GeneratedMedia(
                        content=generated.content,
                        media_type=generated.media_type,
                        usage_cost=provider_job.usage_cost,
                        transcript=generated.transcript,
                    )
                return self._persist_generated(prepared, generated)
            job = self._update_video(prepared, provider_job)
            return MediaGenerationResult(job=job, artifact=None)
        except BaseException as error:
            self._record_failure(prepared.job.id, prepared.run.id, error)
            raise

    def cancel_video(
        self,
        *,
        job_id: UUID,
        expected_revision: int,
        command_run: CommandRun,
    ) -> MediaGenerationResult:
        prepared = self._prepare_existing(
            job_id=job_id,
            command_run=command_run,
            command_name="media.videos.cancel",
        )
        if prepared.job.revision != expected_revision:
            raise VersionConflictError("Media job revision changed")
        if prepared.job.status is MediaGenerationStatus.CANCELLED:
            return MediaGenerationResult(job=prepared.job, artifact=None)
        if prepared.job.is_terminal:
            raise VersionConflictError("Completed media jobs cannot be cancelled")
        with self._unit_of_work_factory() as unit_of_work:
            job = _require_job(unit_of_work, job_id)
            expected_status = job.status
            previous_revision = job.revision
            job.cancel()
            unit_of_work.state.update_media_job(
                job,
                expected_revision=previous_revision,
                expected_status=expected_status,
            )
            run = _require_running(unit_of_work, command_run.id)
            self._append_event(
                unit_of_work,
                run,
                event_type="media.generation.cancelled",
                message="Media generation cancelled",
                payload={"job_id": str(job.id), "kind": job.kind.value},
            )
            unit_of_work.commit()
        return MediaGenerationResult(job=job, artifact=None)

    def recover_interrupted(self) -> dict[str, int]:
        self._staging.cleanup_all()
        interrupted = 0
        resumable_videos = 0
        with self._unit_of_work_factory() as unit_of_work:
            for job in unit_of_work.state.recoverable_media_jobs():
                if (
                    job.kind is MediaGenerationKind.VIDEO
                    and job.provider_job_id is not None
                    and job.status
                    in {MediaGenerationStatus.PENDING, MediaGenerationStatus.IN_PROGRESS}
                ):
                    resumable_videos += 1
                    continue
                expected_status = job.status
                previous_revision = job.revision
                job.interrupt()
                unit_of_work.state.update_media_job(
                    job,
                    expected_revision=previous_revision,
                    expected_status=expected_status,
                )
                interrupted += 1
            if interrupted:
                unit_of_work.commit()
        return {"interrupted": interrupted, "resumable_videos": resumable_videos}

    def _prepare_job(
        self,
        *,
        task_id: UUID,
        command_run: CommandRun,
        command_name: str,
        kind: MediaGenerationKind,
        model_id: str,
        endpoint_kind: ModelEndpointKind,
        output_path: str,
        request_spec: dict[str, object],
        idempotency_key: str,
    ) -> _PreparedJob:
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            scope = self._scope_resolver(unit_of_work.state, task)
            run = _require_running(unit_of_work, command_run.id)
            _validate_run(run, command_run, command_name=command_name, scope=scope)
            if scope.target_version_id is None:
                raise ValueError("media generation requires a writable Workspace Version")
            workspace = unit_of_work.state.get_workspace(scope.workspace_id)
            if workspace is None:
                raise KeyError(f"Workspace not found: {scope.workspace_id}")
            invocation = unit_of_work.assistant.find_tool_invocation_by_command_run_id(run.id)
            if run.actor == "assistant" and invocation is None:
                raise RuntimeError("Assistant media command has no durable Tool Invocation")
            turn = (
                unit_of_work.assistant.get_turn(invocation.turn_id)
                if invocation is not None
                else None
            )
            if invocation is not None and (
                invocation.task_id != task.id
                or invocation.scope_digest != scope.scope_digest
                or invocation.tool_name != command_name
            ):
                raise RuntimeError("Media Tool Invocation does not match its CommandRun")
            self._validate_turn_route(turn, model_id)
            selection = (
                turn.model_selection
                if turn is not None and turn.model_selection is not None
                else unit_of_work.model_catalog.get_selection()
            )
            self._require_model_available(unit_of_work, model_id)
            fingerprint = _request_fingerprint(
                scope=scope,
                kind=kind,
                model_id=model_id,
                endpoint_kind=endpoint_kind,
                output_path=output_path,
                request_spec=request_spec,
            )
            existing = unit_of_work.state.find_media_job_by_idempotency_key(idempotency_key)
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise IdempotencyConflictError("Media idempotency key was reused")
                return _PreparedJob(
                    job=existing,
                    scope=scope,
                    run=run,
                    invocation=invocation,
                    zero_data_retention=selection.zero_data_retention,
                    max_workspace_bytes=workspace.max_bytes,
                )
            job = MediaGenerationJob.create(
                project_id=scope.project_id,
                workspace_id=scope.workspace_id,
                conversation_id=scope.conversation_id,
                task_id=scope.task_id,
                version_id=scope.target_version_id,
                turn_id=turn.id if turn is not None else None,
                command_run_id=run.id,
                scope_digest=scope.scope_digest,
                kind=kind,
                model_id=model_id,
                endpoint_kind=endpoint_kind,
                output_path=output_path,
                request_spec=request_spec,
                request_fingerprint=fingerprint,
                idempotency_key=idempotency_key,
            )
            unit_of_work.state.save_media_job(job)
            unit_of_work.commit()
            return _PreparedJob(
                job=job,
                scope=scope,
                run=run,
                invocation=invocation,
                zero_data_retention=selection.zero_data_retention,
                max_workspace_bytes=workspace.max_bytes,
            )

    def _prepare_existing(
        self,
        *,
        job_id: UUID,
        command_run: CommandRun,
        command_name: str,
    ) -> _PreparedJob:
        with self._unit_of_work_factory() as unit_of_work:
            job = _require_job(unit_of_work, job_id)
            task = unit_of_work.state.get_task(job.task_id)
            if task is None:
                raise KeyError(f"task not found: {job.task_id}")
            scope = self._scope_resolver(unit_of_work.state, task)
            run = _require_running(unit_of_work, command_run.id)
            _validate_run(run, command_run, command_name=command_name, scope=scope)
            if (
                job.kind is not MediaGenerationKind.VIDEO
                or job.scope_digest != scope.scope_digest
                or job.version_id != scope.target_version_id
            ):
                raise RuntimeError("Media job does not match the current Task Scope")
            workspace = unit_of_work.state.get_workspace(job.workspace_id)
            if workspace is None:
                raise KeyError(f"Workspace not found: {job.workspace_id}")
            invocation = unit_of_work.assistant.find_tool_invocation_by_command_run_id(
                job.command_run_id
            )
            selection = unit_of_work.model_catalog.get_selection()
            return _PreparedJob(
                job=job,
                scope=scope,
                run=run,
                invocation=invocation,
                zero_data_retention=selection.zero_data_retention,
                max_workspace_bytes=workspace.max_bytes,
            )

    def _begin(self, prepared: _PreparedJob) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            job = _require_job(unit_of_work, prepared.job.id)
            if job.status is not MediaGenerationStatus.CREATED:
                raise VersionConflictError("Media job is already executing")
            previous_revision = job.revision
            job.begin()
            unit_of_work.state.update_media_job(
                job,
                expected_revision=previous_revision,
                expected_status=MediaGenerationStatus.CREATED,
            )
            run = _require_running(unit_of_work, prepared.run.id)
            self._append_event(
                unit_of_work,
                run,
                event_type="media.generation.started",
                message="Media generation started",
                payload={
                    "job_id": str(job.id),
                    "kind": job.kind.value,
                    "output_path": job.output_path,
                },
            )
            unit_of_work.commit()

    def _provider_call(
        self,
        prepared: _PreparedJob,
        operation: Callable[[], _T],
    ) -> _T:
        observer = None
        if prepared.invocation is not None:
            observer = self._provider_attempts.observer(
                prepared.invocation.turn_id,
                prepared.invocation.model_round,
            )
            observer(
                ProviderAttemptEvent(
                    profile_id=self._provider.account_id,
                    model_id=prepared.job.model_id,
                    endpoint_kind=prepared.job.endpoint_kind.value,
                    model_role=ModelExecutionRole.PRIMARY,
                    attempt_number=1,
                    status=ProviderAttemptStatus.STARTED,
                )
            )
        try:
            result = operation()
        except ProviderError as error:
            if observer is not None:
                observer(
                    ProviderAttemptEvent(
                        profile_id=self._provider.account_id,
                        model_id=prepared.job.model_id,
                        endpoint_kind=prepared.job.endpoint_kind.value,
                        model_role=ModelExecutionRole.PRIMARY,
                        attempt_number=1,
                        status=ProviderAttemptStatus.FAILED,
                        error_category=_error_category(error),
                    )
                )
            raise
        if observer is not None:
            observer(
                ProviderAttemptEvent(
                    profile_id=self._provider.account_id,
                    model_id=prepared.job.model_id,
                    endpoint_kind=prepared.job.endpoint_kind.value,
                    model_role=ModelExecutionRole.PRIMARY,
                    attempt_number=1,
                    status=ProviderAttemptStatus.SUCCEEDED,
                    usage_cost=getattr(result, "usage_cost", None),
                )
            )
        return result

    def _bind_video(
        self,
        prepared: _PreparedJob,
        provider_job: VideoProviderJob,
    ) -> MediaGenerationJob:
        status = _video_status(provider_job.status)
        if status not in {MediaGenerationStatus.PENDING, MediaGenerationStatus.IN_PROGRESS}:
            raise ProviderProtocolError("video start did not return an active job")
        with self._unit_of_work_factory() as unit_of_work:
            job = _require_job(unit_of_work, prepared.job.id)
            previous_revision = job.revision
            previous_status = job.status
            job.usage_cost = provider_job.usage_cost
            job.bind_video(provider_job_id=provider_job.provider_job_id, status=status)
            unit_of_work.state.update_media_job(
                job,
                expected_revision=previous_revision,
                expected_status=previous_status,
            )
            run = _require_running(unit_of_work, prepared.run.id)
            self._append_event(
                unit_of_work,
                run,
                event_type="media.generation.progress",
                message="Video generation queued",
                payload={"job_id": str(job.id), "kind": job.kind.value, "progress": job.progress},
            )
            unit_of_work.commit()
        return job

    def _update_video(
        self,
        prepared: _PreparedJob,
        provider_job: VideoProviderJob,
    ) -> MediaGenerationJob:
        with self._unit_of_work_factory() as unit_of_work:
            job = _require_job(unit_of_work, prepared.job.id)
            previous_revision = job.revision
            previous_status = job.status
            job.usage_cost = provider_job.usage_cost or job.usage_cost
            if provider_job.status is MediaProviderVideoStatus.EXPIRED:
                job.fail(provider_job.error_code or "VIDEO_JOB_EXPIRED", usage_cost=job.usage_cost)
            elif provider_job.status is MediaProviderVideoStatus.FAILED:
                job.fail(
                    provider_job.error_code or "MEDIA_GENERATION_FAILED",
                    usage_cost=job.usage_cost,
                )
            elif provider_job.status is MediaProviderVideoStatus.CANCELLED:
                job.update_video(status=MediaGenerationStatus.CANCELLED, progress=job.progress)
            else:
                status = _video_status(provider_job.status)
                progress = 5 if status is MediaGenerationStatus.PENDING else max(25, job.progress)
                job.update_video(status=status, progress=progress)
            unit_of_work.state.update_media_job(
                job,
                expected_revision=previous_revision,
                expected_status=previous_status,
            )
            run = _require_running(unit_of_work, prepared.run.id)
            event_type = (
                "media.generation.failed"
                if job.status is MediaGenerationStatus.FAILED
                else "media.generation.cancelled"
                if job.status is MediaGenerationStatus.CANCELLED
                else "media.generation.progress"
            )
            self._append_event(
                unit_of_work,
                run,
                event_type=event_type,
                message="Video generation status updated",
                payload={
                    "job_id": str(job.id),
                    "kind": job.kind.value,
                    "progress": job.progress,
                    "status": job.status.value,
                },
            )
            unit_of_work.commit()
        return job

    def _persist_generated(
        self,
        prepared: _PreparedJob,
        generated: GeneratedMedia,
    ) -> MediaGenerationResult:
        _validate_media_output(prepared.job, generated)
        content_hash = hashlib.sha256(generated.content).hexdigest()
        staged = self._staging.stage(
            job_id=prepared.job.id,
            content=generated.content,
            expected_hash=content_hash,
        )
        try:
            with self._unit_of_work_factory() as unit_of_work:
                job = _require_job(unit_of_work, prepared.job.id)
                if job.status is MediaGenerationStatus.COMPLETED:
                    artifact = unit_of_work.state.get_artifact(job.artifact_id)
                    if artifact is None:
                        raise RuntimeError("Completed media job has no Artifact")
                    return MediaGenerationResult(job=job, artifact=artifact)
                existing_index = unit_of_work.project_indexes.get(job.version_id)
                expected_generation = existing_index.generation if existing_index else 0
            workspace_object = self._workspaces.import_asset(
                project_id=prepared.job.workspace_id,
                version_id=prepared.job.version_id,
                mutation=AssetMutation(
                    operation="create",
                    path=prepared.job.output_path,
                    source=staged,
                    expected_source_hash=content_hash,
                ),
                max_file_bytes=min(DEFAULT_MAX_FILE_BYTES, prepared.max_workspace_bytes),
                max_workspace_bytes=prepared.max_workspace_bytes,
            )
            root = self._workspaces.version_path(
                prepared.job.workspace_id,
                prepared.job.version_id,
            ).resolve(strict=True)
            index = self._indexer.build(
                project_id=prepared.job.project_id,
                workspace_id=prepared.job.workspace_id,
                version_id=prepared.job.version_id,
                root=root,
                generation=expected_generation + 1,
            )
            with self._unit_of_work_factory() as unit_of_work:
                job = _require_job(unit_of_work, prepared.job.id)
                previous_revision = job.revision
                previous_status = job.status
                if job.is_terminal:
                    raise VersionConflictError("Media job became terminal during import")
                unit_of_work.project_indexes.replace_generation(
                    index,
                    expected_generation=expected_generation,
                )
                artifact = Artifact.restore(
                    id=job.artifact_id,
                    project_id=job.project_id,
                    conversation_id=job.conversation_id,
                    task_id=job.task_id,
                    version_id=job.version_id,
                    artifact_type=_artifact_type(job.kind),
                    visibility=ArtifactVisibility.CONVERSATION,
                    storage_location=(
                        f"workspace://{job.workspace_id}/{job.version_id}/{job.output_path}"
                    ),
                    media_type=generated.media_type,
                    byte_length=workspace_object.byte_length,
                    content_hash=workspace_object.content_hash,
                    metadata={
                        "job_id": str(job.id),
                        "kind": job.kind.value,
                        "output_path": job.output_path,
                        "prompt_digest": hashlib.sha256(
                            str(job.request_spec["prompt"]).encode("utf-8")
                        ).hexdigest(),
                        "transcript": generated.transcript,
                    },
                    created_at=datetime.now(UTC),
                )
                unit_of_work.state.append_artifact(artifact)
                job.complete(usage_cost=generated.usage_cost or job.usage_cost)
                unit_of_work.state.update_media_job(
                    job,
                    expected_revision=previous_revision,
                    expected_status=previous_status,
                )
                run = _require_running(unit_of_work, prepared.run.id)
                self._append_event(
                    unit_of_work,
                    run,
                    event_type="media.generation.completed",
                    message="Media generation completed",
                    payload={
                        "job_id": str(job.id),
                        "kind": job.kind.value,
                        "artifact_id": str(artifact.id),
                        "output_path": job.output_path,
                    },
                )
                unit_of_work.commit()
            return MediaGenerationResult(job=job, artifact=artifact)
        finally:
            self._staging.cleanup(prepared.job.id)

    def _record_failure(self, job_id: UUID, run_id: UUID, error: BaseException) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            job = _require_job(unit_of_work, job_id)
            if job.is_terminal:
                return
            previous_revision = job.revision
            previous_status = job.status
            if isinstance(error, ProviderCancelledError):
                job.cancel()
                event_type = "media.generation.cancelled"
                message = "Media generation cancelled"
            else:
                job.fail(_error_code(error), usage_cost=job.usage_cost)
                event_type = "media.generation.failed"
                message = "Media generation failed"
            unit_of_work.state.update_media_job(
                job,
                expected_revision=previous_revision,
                expected_status=previous_status,
            )
            run = unit_of_work.commands.get_run(run_id)
            if run is not None and run.status is CommandStatus.RUNNING:
                self._append_event(
                    unit_of_work,
                    run,
                    event_type=event_type,
                    message=message,
                    payload={
                        "job_id": str(job.id),
                        "kind": job.kind.value,
                        "error_code": job.error_code,
                    },
                )
            unit_of_work.commit()

    def _terminal_result(
        self,
        job: MediaGenerationJob,
        *,
        include_active_video: bool = False,
    ) -> MediaGenerationResult | None:
        if (
            include_active_video
            and job.kind is MediaGenerationKind.VIDEO
            and (
                job.provider_job_id is not None
                and job.status in {MediaGenerationStatus.PENDING, MediaGenerationStatus.IN_PROGRESS}
            )
        ):
            return MediaGenerationResult(job=job, artifact=None)
        if not job.is_terminal:
            return None
        if job.status is not MediaGenerationStatus.COMPLETED:
            raise RuntimeError(job.error_code or f"media job is {job.status.value}")
        with self._unit_of_work_factory() as unit_of_work:
            artifact = unit_of_work.state.get_artifact(job.artifact_id)
        if artifact is None:
            raise RuntimeError("Completed media job has no Artifact")
        return MediaGenerationResult(job=job, artifact=artifact)

    @staticmethod
    def _validate_turn_route(turn, model_id: str) -> None:
        if turn is None:
            return
        decision = turn.routing_decision
        if decision is not None and decision.media_model_id is not None:
            if decision.media_model_id != model_id:
                raise RuntimeError("Media model does not match the durable Routing Decision")
            return
        selection = turn.model_selection
        if selection is not None and selection.model_id == model_id:
            return
        raise RuntimeError("Assistant Turn did not authorize this media model")

    @staticmethod
    def _require_model_available(unit_of_work, model_id: str) -> None:
        catalog = unit_of_work.model_catalog.get_catalog()
        if catalog is None:
            return
        entry = next((item for item in catalog.entries if item.model_id == model_id), None)
        if entry is None or entry.availability is ModelAvailability.UNAVAILABLE:
            raise ProviderUnavailableError("selected media model is unavailable")

    @staticmethod
    def _append_event(
        unit_of_work,
        run: CommandRun,
        *,
        event_type: str,
        message: str,
        payload: dict[str, object],
    ) -> None:
        unit_of_work.commands.append_event(
            run_id=run.id,
            event_type=event_type,
            visibility=EventVisibility.USER,
            message=message,
            payload=payload,
            lease_owner=run.lease_owner,
            lease_fence=run.lease_fence,
        )


def _request_fingerprint(
    *,
    scope: ScopeContract,
    kind: MediaGenerationKind,
    model_id: str,
    endpoint_kind: ModelEndpointKind,
    output_path: str,
    request_spec: dict[str, object],
) -> str:
    payload = {
        "scope_digest": scope.scope_digest,
        "kind": kind.value,
        "model_id": model_id,
        "endpoint_kind": endpoint_kind.value,
        "output_path": output_path,
        "request_spec": request_spec,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _default_output_path(kind: str, idempotency_key: str, suffix: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:20]
    return f"generated/{kind}-{digest}{suffix}"


def _validate_run(
    persisted: CommandRun,
    supplied: CommandRun,
    *,
    command_name: str,
    scope: ScopeContract,
) -> None:
    if (
        persisted != supplied
        or persisted.status is not CommandStatus.RUNNING
        or persisted.command_name != command_name
        or persisted.task_id != scope.task_id
        or persisted.conversation_id != scope.conversation_id
        or persisted.project_id != scope.project_id
        or persisted.scope_digest != scope.scope_digest
    ):
        raise RuntimeError("Media CommandRun does not match the current Task Scope")


def _require_running(unit_of_work, run_id: UUID) -> CommandRun:
    run = unit_of_work.commands.get_run(run_id)
    if run is None or run.status is not CommandStatus.RUNNING:
        raise RuntimeError("media generation requires an active CommandRun")
    return run


def _require_job(unit_of_work, job_id: UUID) -> MediaGenerationJob:
    job = unit_of_work.state.get_media_job(job_id)
    if job is None:
        raise KeyError(f"Media generation job not found: {job_id}")
    return job


def _artifact_type(kind: MediaGenerationKind) -> ArtifactType:
    return {
        MediaGenerationKind.IMAGE: ArtifactType.GENERATED_IMAGE,
        MediaGenerationKind.MUSIC: ArtifactType.GENERATED_AUDIO,
        MediaGenerationKind.VIDEO: ArtifactType.GENERATED_VIDEO,
    }[kind]


def _video_status(status: MediaProviderVideoStatus) -> MediaGenerationStatus:
    return {
        MediaProviderVideoStatus.PENDING: MediaGenerationStatus.PENDING,
        MediaProviderVideoStatus.IN_PROGRESS: MediaGenerationStatus.IN_PROGRESS,
        MediaProviderVideoStatus.COMPLETED: MediaGenerationStatus.COMPLETED,
        MediaProviderVideoStatus.FAILED: MediaGenerationStatus.FAILED,
        MediaProviderVideoStatus.CANCELLED: MediaGenerationStatus.CANCELLED,
        MediaProviderVideoStatus.EXPIRED: MediaGenerationStatus.FAILED,
    }[status]


def _validate_media_output(job: MediaGenerationJob, generated: GeneratedMedia) -> None:
    allowed = {
        MediaGenerationKind.IMAGE: {
            "image/png": (".png",),
            "image/jpeg": (".jpg", ".jpeg"),
            "image/webp": (".webp",),
        },
        MediaGenerationKind.MUSIC: {"audio/wav": (".wav",)},
        MediaGenerationKind.VIDEO: {
            "video/mp4": (".mp4",),
            "video/webm": (".webm",),
        },
    }[job.kind]
    suffixes = allowed.get(generated.media_type)
    if suffixes is None or not job.output_path.casefold().endswith(suffixes):
        raise ProviderProtocolError("generated media type does not match the output path")


def _error_category(error: ProviderError) -> ProviderErrorCategory:
    if isinstance(error, ProviderAuthenticationError):
        return ProviderErrorCategory.AUTHENTICATION
    if isinstance(error, ProviderRateLimitError):
        return ProviderErrorCategory.RATE_LIMIT
    if isinstance(error, ProviderTimeoutError):
        return ProviderErrorCategory.TIMEOUT
    if isinstance(error, ProviderProtocolError):
        return ProviderErrorCategory.PROTOCOL
    if isinstance(error, ProviderContentRejectedError):
        return ProviderErrorCategory.CONTENT_REJECTED
    if isinstance(error, ProviderNetworkError):
        return ProviderErrorCategory.NETWORK
    if isinstance(error, ProviderUnavailableError):
        return ProviderErrorCategory.UNAVAILABLE
    if isinstance(error, ProviderCancelledError):
        return ProviderErrorCategory.CANCELLED
    return ProviderErrorCategory.UNKNOWN


def _error_code(error: BaseException) -> str:
    if isinstance(error, ProviderAuthenticationError):
        return "PROVIDER_AUTH_REJECTED"
    if isinstance(error, ProviderRateLimitError):
        return "PROVIDER_RATE_LIMITED"
    if isinstance(error, ProviderTimeoutError):
        return "PROVIDER_TIMEOUT"
    if isinstance(error, ProviderContentRejectedError):
        return "PROVIDER_CONTENT_REJECTED"
    if isinstance(error, ProviderNetworkError):
        return "PROVIDER_NETWORK_ERROR"
    if isinstance(error, ProviderUnavailableError):
        return "PROVIDER_UNAVAILABLE"
    if isinstance(error, ProviderProtocolError):
        return "PROVIDER_PROTOCOL_ERROR"
    code = getattr(error, "error_code", None)
    if isinstance(code, str) and code and len(code) <= 128:
        return code
    return "MEDIA_GENERATION_FAILED"


def _prompt(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 20_000:
        raise ValueError("media prompt must contain between 1 and 20,000 characters")
    return normalized


def _choice(value: object, allowed: set[str], name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"unsupported {name}")
    return value


def _seed(value: int | None) -> int | None:
    if value is None:
        return None
    return _integer_range(value, 0, 2_147_483_647, "media seed")


def _integer_range(value: object, minimum: int, maximum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside its allowed range")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


__all__ = [
    "IMAGE_MODEL_ID",
    "MUSIC_MODEL_ID",
    "VIDEO_MODEL_ID",
    "MediaApplication",
    "MediaGenerationResult",
]
