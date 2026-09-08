from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import PurePosixPath
from uuid import UUID

from fairy_core.commanding import CommandRun, CommandStatus
from fairy_core.domain.execution import ArtifactType
from fairy_core.domain.models import ScopeContract
from fairy_core.media.models import (
    MediaGenerationJob,
    MediaGenerationKind,
    MediaGenerationStatus,
)
from fairy_core.media.ports import GeneratedMedia, MediaProviderVideoStatus
from fairy_core.model_catalog.models import ModelEndpointKind
from fairy_core.providers import (
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


class MediaOutputLimitError(RuntimeError):
    error_code = "MEDIA_OUTPUT_LIMIT_REACHED"
    model_detail = (
        "The planned media output was already attempted in this plan revision, or an earlier "
        "output is still active. Do not create another media job; report the existing result "
        "and wait for an explicit user request after that operation settles."
    )


class MediaJobFailedError(RuntimeError):
    model_detail = "Media generation failed. Do not request another output in this Turn."

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


def request_fingerprint(
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


def compatible_request_fingerprints(
    *,
    scope: ScopeContract,
    kind: MediaGenerationKind,
    model_id: str,
    endpoint_kind: ModelEndpointKind,
    output_path: str,
    request_spec: dict[str, object],
    stored_spec: dict[str, object],
) -> frozenset[str]:
    specs = [request_spec, stored_spec]
    if "output_path_auto" in request_spec:
        for value in (request_spec, stored_spec):
            legacy = dict(value)
            legacy.pop("output_path_auto")
            specs.append(legacy)
    return frozenset(
        request_fingerprint(
            scope=scope,
            kind=kind,
            model_id=model_id,
            endpoint_kind=endpoint_kind,
            output_path=output_path,
            request_spec=value,
        )
        for value in specs
    )


def default_output_path(kind: str, idempotency_key: str, suffix: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:20]
    return f"generated/{kind}-{digest}{suffix}"


def stored_zdr(request_spec: Mapping[str, object], *, fallback: bool) -> bool:
    value = request_spec.get("zero_data_retention")
    if value is None:
        return fallback
    if not isinstance(value, bool):
        raise RuntimeError("Media Job has an invalid ZDR snapshot")
    return value


def stored_seed(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError("Media Job has an invalid seed")
    return value


def validate_run(
    persisted: CommandRun,
    supplied: CommandRun,
    *,
    command_name: str,
    scope: ScopeContract,
) -> None:
    if (
        persisted != supplied
        or persisted.status not in {CommandStatus.QUEUED, CommandStatus.RUNNING}
        or persisted.command_name != command_name
        or persisted.task_id != scope.task_id
        or persisted.conversation_id != scope.conversation_id
        or persisted.project_id != scope.project_id
        or persisted.scope_digest != scope.scope_digest
    ):
        raise RuntimeError("Media CommandRun does not match the current Task Scope")


def require_running(unit_of_work, run_id: UUID) -> CommandRun:
    run = unit_of_work.commands.get_run(run_id)
    if run is None or run.status is not CommandStatus.RUNNING:
        raise RuntimeError("media generation requires an active CommandRun")
    return run


def require_preparable_run(unit_of_work, run_id: UUID) -> CommandRun:
    run = unit_of_work.commands.get_run(run_id)
    if run is None or run.status not in {CommandStatus.QUEUED, CommandStatus.RUNNING}:
        raise RuntimeError("media generation requires a queued or active CommandRun")
    return run


def require_event_run(unit_of_work, run_id: UUID) -> CommandRun:
    run = unit_of_work.commands.get_run(run_id)
    if run is None or run.status not in {CommandStatus.RUNNING, CommandStatus.SUCCEEDED}:
        raise RuntimeError("media generation lost its accepted CommandRun")
    return run


def require_job(unit_of_work, job_id: UUID) -> MediaGenerationJob:
    job = unit_of_work.state.get_media_job(job_id)
    if job is None:
        raise KeyError(f"Media generation job not found: {job_id}")
    return job


def artifact_type(kind: MediaGenerationKind) -> ArtifactType:
    return {
        MediaGenerationKind.IMAGE: ArtifactType.GENERATED_IMAGE,
        MediaGenerationKind.MUSIC: ArtifactType.GENERATED_AUDIO,
        MediaGenerationKind.VIDEO: ArtifactType.GENERATED_VIDEO,
    }[kind]


def video_status(status: MediaProviderVideoStatus) -> MediaGenerationStatus:
    return {
        MediaProviderVideoStatus.PENDING: MediaGenerationStatus.PENDING,
        MediaProviderVideoStatus.IN_PROGRESS: MediaGenerationStatus.IN_PROGRESS,
        MediaProviderVideoStatus.COMPLETED: MediaGenerationStatus.COMPLETED,
        MediaProviderVideoStatus.FAILED: MediaGenerationStatus.FAILED,
        MediaProviderVideoStatus.CANCELLED: MediaGenerationStatus.CANCELLED,
        MediaProviderVideoStatus.EXPIRED: MediaGenerationStatus.FAILED,
    }[status]


def validate_media_output(job: MediaGenerationJob, generated: GeneratedMedia) -> None:
    resolved_media_output_path(job, generated)


def resolved_media_output_path(job: MediaGenerationJob, generated: GeneratedMedia) -> str:
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
    if suffixes is None:
        raise ProviderProtocolError("generated media type is unsupported")
    if job.output_path.casefold().endswith(suffixes):
        return job.output_path
    output_path_auto = job.request_spec.get("output_path_auto") is True
    if job.kind is MediaGenerationKind.IMAGE and output_path_auto:
        preferred_suffix = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
        }[generated.media_type]
        return str(PurePosixPath(job.output_path).with_suffix(preferred_suffix))
    raise ProviderProtocolError("generated media type does not match the output path")


def error_category(error: ProviderError) -> ProviderErrorCategory:
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


def error_code(error: BaseException) -> str:
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


def prompt(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 20_000:
        raise ValueError("media prompt must contain between 1 and 20,000 characters")
    return normalized


def choice(value: object, allowed: set[str], name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"unsupported {name}")
    return value


def seed(value: int | None) -> int | None:
    if value is None:
        return None
    return integer_range(value, 0, 2_147_483_647, "media seed")


def integer_range(value: object, minimum: int, maximum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside its allowed range")
    return value


def boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


__all__ = [
    "MediaJobFailedError",
    "MediaOutputLimitError",
    "artifact_type",
    "boolean",
    "choice",
    "compatible_request_fingerprints",
    "default_output_path",
    "error_category",
    "error_code",
    "integer_range",
    "prompt",
    "request_fingerprint",
    "require_event_run",
    "require_job",
    "require_preparable_run",
    "require_running",
    "resolved_media_output_path",
    "seed",
    "stored_seed",
    "stored_zdr",
    "validate_media_output",
    "validate_run",
    "video_status",
]
