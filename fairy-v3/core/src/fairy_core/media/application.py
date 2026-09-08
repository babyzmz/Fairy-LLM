from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from fairy_core.assistant.models import ToolInvocation
from fairy_core.assistant.provider_attempts import ProviderAttemptRecorder
from fairy_core.assistant.tool_revision import active_tool_revision
from fairy_core.commanding import CommandRun, CommandStatus, EventVisibility
from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.domain.execution import Artifact, ArtifactVisibility
from fairy_core.domain.models import ScopeContract
from fairy_core.media import invariants as media_invariants
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
    ProviderCancelledError,
    ProviderError,
    ProviderProtocolError,
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

    def prepare_image(
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
    ) -> MediaGenerationResult:
        output_path_auto = output_path is None
        spec = {
            "prompt": media_invariants.prompt(prompt),
            "size": media_invariants.choice(size, {"1024x1024"}, "image size"),
            "aspect_ratio": media_invariants.choice(
                aspect_ratio,
                {"1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16"},
                "image aspect ratio",
            ),
            "seed": media_invariants.seed(seed),
            "output_path_auto": output_path_auto,
        }
        prepared = self._prepare_job(
            task_id=task_id,
            command_run=command_run,
            command_name="media.images.generate",
            kind=MediaGenerationKind.IMAGE,
            model_id=IMAGE_MODEL_ID,
            endpoint_kind=ModelEndpointKind.IMAGES,
            output_path=output_path
            or media_invariants.default_output_path("image", idempotency_key, ".png"),
            request_spec=spec,
            idempotency_key=idempotency_key,
        )
        return self._terminal_result(prepared.job) or MediaGenerationResult(
            job=prepared.job,
            artifact=None,
        )

    def prepare_music(
        self,
        *,
        task_id: UUID,
        command_run: CommandRun,
        prompt: str,
        output_path: str | None,
        output_format: str,
        seed: int | None,
        idempotency_key: str,
    ) -> MediaGenerationResult:
        spec = {
            "prompt": media_invariants.prompt(prompt),
            "output_format": media_invariants.choice(output_format, {"wav"}, "music format"),
            "seed": media_invariants.seed(seed),
        }
        prepared = self._prepare_job(
            task_id=task_id,
            command_run=command_run,
            command_name="media.audio.generate",
            kind=MediaGenerationKind.MUSIC,
            model_id=MUSIC_MODEL_ID,
            endpoint_kind=ModelEndpointKind.AUDIO,
            output_path=output_path
            or media_invariants.default_output_path("music", idempotency_key, ".wav"),
            request_spec=spec,
            idempotency_key=idempotency_key,
        )
        return self._terminal_result(prepared.job) or MediaGenerationResult(
            job=prepared.job,
            artifact=None,
        )

    def prepare_video(
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
    ) -> MediaGenerationResult:
        spec = {
            "prompt": media_invariants.prompt(prompt),
            "duration_seconds": media_invariants.integer_range(
                duration_seconds, 1, 20, "video duration"
            ),
            "resolution": media_invariants.choice(
                resolution, {"720p", "1080p"}, "video resolution"
            ),
            "aspect_ratio": media_invariants.choice(
                aspect_ratio,
                {"1:1", "16:9", "9:16"},
                "video aspect ratio",
            ),
            "generate_audio": media_invariants.boolean(generate_audio, "generate_audio"),
            "seed": media_invariants.seed(seed),
        }
        prepared = self._prepare_job(
            task_id=task_id,
            command_run=command_run,
            command_name="media.videos.start",
            kind=MediaGenerationKind.VIDEO,
            model_id=VIDEO_MODEL_ID,
            endpoint_kind=ModelEndpointKind.VIDEOS,
            output_path=output_path
            or media_invariants.default_output_path("video", idempotency_key, ".mp4"),
            request_spec=spec,
            idempotency_key=idempotency_key,
        )
        return self._terminal_result(
            prepared.job,
            include_active_video=True,
        ) or MediaGenerationResult(job=prepared.job, artifact=None)

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
        prepared = self.prepare_image(
            task_id=task_id,
            command_run=command_run,
            prompt=prompt,
            output_path=output_path,
            size=size,
            aspect_ratio=aspect_ratio,
            seed=seed,
            idempotency_key=idempotency_key,
        )
        if prepared.artifact is not None:
            return prepared
        return self.execute_job(prepared.job.id, cancellation=cancellation)

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
        prepared = self.prepare_music(
            task_id=task_id,
            command_run=command_run,
            prompt=prompt,
            output_path=output_path,
            output_format=output_format,
            seed=seed,
            idempotency_key=idempotency_key,
        )
        if prepared.artifact is not None:
            return prepared
        return self.execute_job(prepared.job.id, cancellation=cancellation)

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
        prepared = self.prepare_video(
            task_id=task_id,
            command_run=command_run,
            prompt=prompt,
            output_path=output_path,
            duration_seconds=duration_seconds,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            generate_audio=generate_audio,
            seed=seed,
            idempotency_key=idempotency_key,
        )
        if prepared.job.provider_job_id is not None or prepared.job.is_terminal:
            return prepared
        return self.execute_job(prepared.job.id, cancellation=cancellation)

    def execute_job(
        self,
        job_id: UUID,
        *,
        cancellation: CancellationToken,
        attempt_number: int = 1,
    ) -> MediaGenerationResult:
        prepared = self._prepare_worker_job(job_id)
        replay = self._terminal_result(
            prepared.job,
            include_active_video=False,
        )
        if replay is not None:
            return replay
        try:
            if prepared.job.status is MediaGenerationStatus.CREATED:
                self._begin(prepared)
                prepared = self._prepare_worker_job(job_id)
            spec = prepared.job.request_spec
            if prepared.job.kind is MediaGenerationKind.IMAGE:
                generated = self._provider_call(
                    prepared,
                    lambda: self._provider.generate_image(
                        ImageGenerationRequest(
                            model_id=prepared.job.model_id,
                            prompt=str(spec["prompt"]),
                            size=str(spec["size"]),
                            aspect_ratio=str(spec["aspect_ratio"]),
                            seed=media_invariants.stored_seed(spec.get("seed")),
                            idempotency_key=prepared.job.idempotency_key,
                            zero_data_retention=prepared.zero_data_retention,
                        ),
                        cancellation,
                    ),
                    attempt_number=attempt_number,
                )
                cancellation.raise_if_cancelled()
                return self._persist_generated(prepared, generated)
            if prepared.job.kind is MediaGenerationKind.MUSIC:
                generated = self._provider_call(
                    prepared,
                    lambda: self._provider.generate_music(
                        MusicGenerationRequest(
                            model_id=prepared.job.model_id,
                            prompt=str(spec["prompt"]),
                            output_format=str(spec["output_format"]),
                            seed=media_invariants.stored_seed(spec.get("seed")),
                            idempotency_key=prepared.job.idempotency_key,
                            zero_data_retention=prepared.zero_data_retention,
                        ),
                        cancellation,
                    ),
                    attempt_number=attempt_number,
                )
                cancellation.raise_if_cancelled()
                return self._persist_generated(prepared, generated)
            return self._execute_video(
                prepared,
                cancellation=cancellation,
                attempt_number=attempt_number,
            )
        except BaseException as error:
            if not cancellation.is_interrupted:
                self._record_failure(prepared.job.id, prepared.run.id, error)
            raise

    def _execute_video(
        self,
        prepared: _PreparedJob,
        *,
        cancellation: CancellationToken,
        attempt_number: int,
    ) -> MediaGenerationResult:
        spec = prepared.job.request_spec
        if prepared.job.provider_job_id is None:
            if prepared.zero_data_retention:
                raise ProviderUnavailableError("OpenRouter video generation does not support ZDR")
            provider_job = self._provider_call(
                prepared,
                lambda: self._provider.start_video(
                    VideoGenerationRequest(
                        model_id=prepared.job.model_id,
                        prompt=str(spec["prompt"]),
                        duration_seconds=int(spec["duration_seconds"]),
                        resolution=str(spec["resolution"]),
                        aspect_ratio=str(spec["aspect_ratio"]),
                        generate_audio=bool(spec["generate_audio"]),
                        seed=media_invariants.stored_seed(spec.get("seed")),
                        idempotency_key=prepared.job.idempotency_key,
                        zero_data_retention=False,
                    ),
                    cancellation,
                ),
                attempt_number=attempt_number,
            )
            job = self._bind_video(prepared, provider_job)
            return MediaGenerationResult(job=job, artifact=None)
        provider_job = self._provider_call(
            prepared,
            lambda: self._provider.get_video(
                prepared.job.provider_job_id or "",
                cancellation,
            ),
            attempt_number=attempt_number,
        )
        if provider_job.status is MediaProviderVideoStatus.COMPLETED:
            generated = self._provider_call(
                prepared,
                lambda: self._provider.download_video(
                    prepared.job.provider_job_id or "",
                    cancellation,
                ),
                attempt_number=attempt_number,
            )
            if generated.usage_cost is None and provider_job.usage_cost is not None:
                generated = GeneratedMedia(
                    content=generated.content,
                    media_type=generated.media_type,
                    usage_cost=provider_job.usage_cost,
                    transcript=generated.transcript,
                )
            cancellation.raise_if_cancelled()
            return self._persist_generated(prepared, generated)
        job = self._update_video(prepared, provider_job)
        return MediaGenerationResult(job=job, artifact=None)

    def poll_video(
        self,
        *,
        job_id: UUID,
        command_run: CommandRun,
        cancellation: CancellationToken,
    ) -> MediaGenerationResult:
        self._prepare_existing(
            job_id=job_id,
            command_run=command_run,
            command_name="media.videos.poll",
        )
        return self.execute_job(job_id, cancellation=cancellation)

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
            job = media_invariants.require_job(unit_of_work, job_id)
            expected_status = job.status
            previous_revision = job.revision
            job.cancel()
            unit_of_work.state.update_media_job(
                job,
                expected_revision=previous_revision,
                expected_status=expected_status,
            )
            run = media_invariants.require_running(unit_of_work, command_run.id)
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
        resumable_videos = 0
        with self._unit_of_work_factory() as unit_of_work:
            for job in unit_of_work.state.recoverable_media_jobs():
                if job.kind is MediaGenerationKind.VIDEO:
                    resumable_videos += 1
        return {"interrupted": 0, "resumable_videos": resumable_videos}

    def get_result(
        self,
        job_id: UUID,
        *,
        include_active_video: bool,
        raise_terminal_error: bool = True,
    ) -> MediaGenerationResult | None:
        with self._unit_of_work_factory() as unit_of_work:
            job = media_invariants.require_job(unit_of_work, job_id)
        try:
            return self._terminal_result(
                job,
                include_active_video=include_active_video,
            )
        except RuntimeError:
            if raise_terminal_error:
                raise
            return MediaGenerationResult(job=job, artifact=None)

    def cancel_work(self, job_id: UUID) -> MediaGenerationJob:
        with self._unit_of_work_factory() as unit_of_work:
            job = media_invariants.require_job(unit_of_work, job_id)
            if job.is_terminal:
                return job
            previous_revision = job.revision
            previous_status = job.status
            job.cancel()
            unit_of_work.state.update_media_job(
                job,
                expected_revision=previous_revision,
                expected_status=previous_status,
            )
            run = unit_of_work.commands.get_run(job.command_run_id)
            if run is not None and run.status in {
                CommandStatus.RUNNING,
                CommandStatus.SUCCEEDED,
            }:
                self._append_event(
                    unit_of_work,
                    run,
                    event_type="media.generation.cancelled",
                    message="Media generation cancelled",
                    payload={"job_id": str(job.id), "kind": job.kind.value},
                )
            unit_of_work.commit()
        return job

    def fail_work(self, job_id: UUID, *, error_code: str) -> MediaGenerationJob:
        with self._unit_of_work_factory() as unit_of_work:
            job = media_invariants.require_job(unit_of_work, job_id)
            if job.is_terminal:
                return job
            previous_revision = job.revision
            previous_status = job.status
            job.fail(error_code, usage_cost=job.usage_cost)
            unit_of_work.state.update_media_job(
                job,
                expected_revision=previous_revision,
                expected_status=previous_status,
            )
            run = unit_of_work.commands.get_run(job.command_run_id)
            if run is not None:
                self._append_event(
                    unit_of_work,
                    run,
                    event_type="media.generation.failed",
                    message="Media generation failed",
                    payload={
                        "job_id": str(job.id),
                        "kind": job.kind.value,
                        "error_code": job.error_code,
                    },
                )
            unit_of_work.commit()
        return job

    @staticmethod
    def _has_planned_output(unit, turn, invocation, kind):
        run_id, revision = active_tool_revision(unit, turn)
        if invocation is None or (
            invocation.workflow_run_id, invocation.workflow_plan_revision
        ) != (run_id, revision):
            raise RuntimeError("Media dispatch does not own the current Workflow revision")
        calls = {
            item.command_run_id: item
            for item in unit.assistant.list_tool_invocations(turn.id)
        }
        for job in unit.state.list_media_jobs(turn.task_id):
            if job.turn_id != turn.id or job.kind is not kind:
                continue
            previous = calls.get(job.command_run_id)
            if (
                not job.is_terminal or previous is None
                or previous.workflow_plan_revision == revision
            ):
                return True
        return False

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
            run = media_invariants.require_preparable_run(unit_of_work, command_run.id)
            media_invariants.validate_run(run, command_run, command_name=command_name, scope=scope)
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
            stored_spec = {
                **request_spec,
                "zero_data_retention": selection.zero_data_retention,
            }
            fingerprint = media_invariants.request_fingerprint(
                scope=scope,
                kind=kind,
                model_id=model_id,
                endpoint_kind=endpoint_kind,
                output_path=output_path,
                request_spec=stored_spec,
            )
            compatible_fingerprints = media_invariants.compatible_request_fingerprints(
                scope=scope,
                kind=kind,
                model_id=model_id,
                endpoint_kind=endpoint_kind,
                output_path=output_path,
                request_spec=request_spec,
                stored_spec=stored_spec,
            )
            existing = unit_of_work.state.find_media_job_by_idempotency_key(idempotency_key)
            if existing is not None:
                if existing.request_fingerprint not in compatible_fingerprints:
                    raise IdempotencyConflictError("Media idempotency key was reused")
                unit_of_work.commit()
                return _PreparedJob(
                    job=existing,
                    scope=scope,
                    run=run,
                    invocation=invocation,
                    zero_data_retention=media_invariants.stored_zdr(
                        existing.request_spec,
                        fallback=selection.zero_data_retention,
                    ),
                    max_workspace_bytes=workspace.max_bytes,
                )
            if turn is not None and self._has_planned_output(
                unit_of_work, turn, invocation, kind,
            ):
                raise media_invariants.MediaOutputLimitError(
                    "Assistant Turn already owns its planned media output"
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
                request_spec=stored_spec,
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

    def _prepare_worker_job(self, job_id: UUID) -> _PreparedJob:
        with self._unit_of_work_factory() as unit_of_work:
            job = media_invariants.require_job(unit_of_work, job_id)
            task = unit_of_work.state.get_task(job.task_id)
            if task is None:
                raise KeyError(f"task not found: {job.task_id}")
            scope = self._scope_resolver(unit_of_work.state, task)
            if (
                job.scope_digest != scope.scope_digest
                or job.workspace_id != scope.workspace_id
                or job.version_id != scope.target_version_id
                or job.conversation_id != scope.conversation_id
                or job.project_id != scope.project_id
            ):
                raise RuntimeError("Media job does not match the current Task Scope")
            run = unit_of_work.commands.get_run(job.command_run_id)
            if run is None:
                raise RuntimeError("Media Job lost its CommandRun")
            if run.task_id != job.task_id or run.scope_digest != job.scope_digest:
                raise RuntimeError("Media Job CommandRun Scope changed")
            if (
                job.provider_job_id is None
                and not job.is_terminal
                and run.status is not CommandStatus.RUNNING
            ):
                raise RuntimeError("Media provider submission requires an active CommandRun")
            if (
                job.provider_job_id is not None
                and not job.is_terminal
                and run.status not in {CommandStatus.RUNNING, CommandStatus.SUCCEEDED}
            ):
                raise RuntimeError("Video polling requires an accepted CommandRun")
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
                zero_data_retention=media_invariants.stored_zdr(
                    job.request_spec,
                    fallback=selection.zero_data_retention,
                ),
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
            job = media_invariants.require_job(unit_of_work, job_id)
            task = unit_of_work.state.get_task(job.task_id)
            if task is None:
                raise KeyError(f"task not found: {job.task_id}")
            scope = self._scope_resolver(unit_of_work.state, task)
            run = media_invariants.require_running(unit_of_work, command_run.id)
            media_invariants.validate_run(run, command_run, command_name=command_name, scope=scope)
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
            job = media_invariants.require_job(unit_of_work, prepared.job.id)
            if job.status is not MediaGenerationStatus.CREATED:
                raise VersionConflictError("Media job is already executing")
            previous_revision = job.revision
            job.begin()
            unit_of_work.state.update_media_job(
                job,
                expected_revision=previous_revision,
                expected_status=MediaGenerationStatus.CREATED,
            )
            run = media_invariants.require_event_run(unit_of_work, prepared.run.id)
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
        *,
        attempt_number: int = 1,
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
                    attempt_number=attempt_number,
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
                        attempt_number=attempt_number,
                        status=ProviderAttemptStatus.FAILED,
                        error_category=media_invariants.error_category(error),
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
                    attempt_number=attempt_number,
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
        status = media_invariants.video_status(provider_job.status)
        if status not in {MediaGenerationStatus.PENDING, MediaGenerationStatus.IN_PROGRESS}:
            raise ProviderProtocolError("video start did not return an active job")
        with self._unit_of_work_factory() as unit_of_work:
            job = media_invariants.require_job(unit_of_work, prepared.job.id)
            previous_revision = job.revision
            previous_status = job.status
            job.usage_cost = provider_job.usage_cost
            job.bind_video(provider_job_id=provider_job.provider_job_id, status=status)
            unit_of_work.state.update_media_job(
                job,
                expected_revision=previous_revision,
                expected_status=previous_status,
            )
            run = media_invariants.require_event_run(unit_of_work, prepared.run.id)
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
            job = media_invariants.require_job(unit_of_work, prepared.job.id)
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
                status = media_invariants.video_status(provider_job.status)
                progress = 5 if status is MediaGenerationStatus.PENDING else max(25, job.progress)
                job.update_video(status=status, progress=progress)
            unit_of_work.state.update_media_job(
                job,
                expected_revision=previous_revision,
                expected_status=previous_status,
            )
            run = media_invariants.require_event_run(unit_of_work, prepared.run.id)
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
        output_path = media_invariants.resolved_media_output_path(prepared.job, generated)
        prepared.job.output_path = output_path
        content_hash = hashlib.sha256(generated.content).hexdigest()
        staged = self._staging.stage(
            job_id=prepared.job.id,
            content=generated.content,
            expected_hash=content_hash,
        )
        try:
            with self._unit_of_work_factory() as unit_of_work:
                job = media_invariants.require_job(unit_of_work, prepared.job.id)
                if job.status is MediaGenerationStatus.COMPLETED:
                    artifact = unit_of_work.state.get_artifact(job.artifact_id)
                    if artifact is None:
                        raise RuntimeError("Completed media job has no Artifact")
                    return MediaGenerationResult(job=job, artifact=artifact)
                if job.is_terminal:
                    raise VersionConflictError("Media job became terminal before import")
                existing_index = unit_of_work.project_indexes.get(job.version_id)
                expected_generation = existing_index.generation if existing_index else 0
            workspace_object = self._workspaces.import_asset(
                project_id=prepared.job.workspace_id,
                version_id=prepared.job.version_id,
                mutation=AssetMutation(
                    operation="create",
                    path=output_path,
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
            try:
                with self._unit_of_work_factory() as unit_of_work:
                    job = media_invariants.require_job(unit_of_work, prepared.job.id)
                    previous_revision = job.revision
                    previous_status = job.status
                    if job.is_terminal:
                        raise VersionConflictError("Media job became terminal during import")
                    job.output_path = output_path
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
                        artifact_type=media_invariants.artifact_type(job.kind),
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
                    run = media_invariants.require_event_run(unit_of_work, prepared.run.id)
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
            except BaseException:
                self._rollback_materialized_asset(
                    prepared,
                    expected_hash=workspace_object.content_hash,
                )
                raise
            return MediaGenerationResult(job=job, artifact=artifact)
        finally:
            self._staging.cleanup(prepared.job.id)

    def _rollback_materialized_asset(
        self,
        prepared: _PreparedJob,
        *,
        expected_hash: str,
    ) -> None:
        root = self._workspaces.version_path(
            prepared.job.workspace_id,
            prepared.job.version_id,
        ).resolve(strict=True)
        target = (root / prepared.job.output_path).resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError:
            raise RuntimeError("Media rollback path escaped its Workspace") from None
        if not target.is_file():
            return
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected_hash:
            raise RuntimeError("Media rollback target changed after import")
        target.unlink()

    def _record_failure(self, job_id: UUID, run_id: UUID, error: BaseException) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            job = media_invariants.require_job(unit_of_work, job_id)
            if job.is_terminal:
                return
            previous_revision = job.revision
            previous_status = job.status
            if isinstance(error, ProviderCancelledError):
                job.cancel()
                event_type = "media.generation.cancelled"
                message = "Media generation cancelled"
            else:
                job.fail(media_invariants.error_code(error), usage_cost=job.usage_cost)
                event_type = "media.generation.failed"
                message = "Media generation failed"
            unit_of_work.state.update_media_job(
                job,
                expected_revision=previous_revision,
                expected_status=previous_status,
            )
            run = unit_of_work.commands.get_run(run_id)
            if run is not None:
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
            raise media_invariants.MediaJobFailedError(
                job.error_code or f"MEDIA_{job.status.value.upper()}"
            )
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
        lease_owner = run.lease_owner if run.status is CommandStatus.RUNNING else None
        lease_fence = run.lease_fence if run.status is CommandStatus.RUNNING else None
        unit_of_work.commands.append_event(
            run_id=run.id,
            event_type=event_type,
            visibility=EventVisibility.USER,
            message=message,
            payload=payload,
            lease_owner=lease_owner,
            lease_fence=lease_fence,
        )


__all__ = [
    "IMAGE_MODEL_ID",
    "MUSIC_MODEL_ID",
    "VIDEO_MODEL_ID",
    "MediaApplication",
    "MediaGenerationResult",
]
