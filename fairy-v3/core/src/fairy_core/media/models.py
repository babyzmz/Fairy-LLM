from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.ids import new_id
from fairy_core.model_catalog.models import MODEL_ALLOWLIST_BY_ID, ModelEndpointKind


class MediaGenerationKind(StrEnum):
    IMAGE = "image"
    MUSIC = "music"
    VIDEO = "video"


class MediaGenerationStatus(StrEnum):
    CREATED = "created"
    GENERATING = "generating"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


_TERMINAL_STATUSES = frozenset(
    {
        MediaGenerationStatus.COMPLETED,
        MediaGenerationStatus.FAILED,
        MediaGenerationStatus.CANCELLED,
        MediaGenerationStatus.INTERRUPTED,
    }
)

_TRANSITIONS: dict[MediaGenerationStatus, frozenset[MediaGenerationStatus]] = {
    MediaGenerationStatus.CREATED: frozenset(
        {
            MediaGenerationStatus.GENERATING,
            MediaGenerationStatus.PENDING,
            MediaGenerationStatus.IN_PROGRESS,
            MediaGenerationStatus.FAILED,
            MediaGenerationStatus.CANCELLED,
            MediaGenerationStatus.INTERRUPTED,
        }
    ),
    MediaGenerationStatus.GENERATING: frozenset(
        {
            MediaGenerationStatus.PENDING,
            MediaGenerationStatus.IN_PROGRESS,
            MediaGenerationStatus.COMPLETED,
            MediaGenerationStatus.FAILED,
            MediaGenerationStatus.CANCELLED,
            MediaGenerationStatus.INTERRUPTED,
        }
    ),
    MediaGenerationStatus.PENDING: frozenset(
        {
            MediaGenerationStatus.IN_PROGRESS,
            MediaGenerationStatus.COMPLETED,
            MediaGenerationStatus.FAILED,
            MediaGenerationStatus.CANCELLED,
            MediaGenerationStatus.INTERRUPTED,
        }
    ),
    MediaGenerationStatus.IN_PROGRESS: frozenset(
        {
            MediaGenerationStatus.COMPLETED,
            MediaGenerationStatus.FAILED,
            MediaGenerationStatus.CANCELLED,
            MediaGenerationStatus.INTERRUPTED,
        }
    ),
    MediaGenerationStatus.COMPLETED: frozenset(),
    MediaGenerationStatus.FAILED: frozenset(),
    MediaGenerationStatus.CANCELLED: frozenset(),
    MediaGenerationStatus.INTERRUPTED: frozenset(),
}


@dataclass(slots=True)
class MediaGenerationJob:
    id: UUID
    project_id: UUID | None
    workspace_id: UUID
    conversation_id: UUID
    task_id: UUID
    version_id: UUID
    turn_id: UUID | None
    command_run_id: UUID
    scope_digest: str
    kind: MediaGenerationKind
    model_id: str
    endpoint_kind: ModelEndpointKind
    output_path: str
    request_spec: Mapping[str, Any]
    request_fingerprint: str
    idempotency_key: str
    artifact_id: UUID
    status: MediaGenerationStatus = MediaGenerationStatus.CREATED
    provider_job_id: str | None = None
    progress: int = 0
    usage_cost: str | None = None
    error_code: str | None = None
    revision: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        allowed = MODEL_ALLOWLIST_BY_ID.get(self.model_id)
        if allowed is None or allowed.endpoint_kind is not self.endpoint_kind:
            raise ValueError("media job model must match the approved endpoint")
        expected_endpoint = {
            MediaGenerationKind.IMAGE: ModelEndpointKind.IMAGES,
            MediaGenerationKind.MUSIC: ModelEndpointKind.AUDIO,
            MediaGenerationKind.VIDEO: ModelEndpointKind.VIDEOS,
        }[self.kind]
        if self.endpoint_kind is not expected_endpoint:
            raise ValueError("media job kind does not match its endpoint")
        if len(self.scope_digest) != 64 or not _is_sha256(self.scope_digest):
            raise ValueError("media job scope digest is invalid")
        if len(self.request_fingerprint) != 64 or not _is_sha256(self.request_fingerprint):
            raise ValueError("media job request fingerprint is invalid")
        if not self.idempotency_key.strip() or len(self.idempotency_key) > 512:
            raise ValueError("media job idempotency key is invalid")
        if not 0 <= self.progress <= 100:
            raise ValueError("media job progress must be between 0 and 100")
        if self.revision < 0:
            raise ValueError("media job revision cannot be negative")
        if self.status is MediaGenerationStatus.COMPLETED and self.progress != 100:
            raise ValueError("completed media jobs must have full progress")
        if self.provider_job_id is not None and (
            not self.provider_job_id.strip() or len(self.provider_job_id) > 255
        ):
            raise ValueError("media provider job id is invalid")
        if self.error_code is not None and (
            not self.error_code.strip() or len(self.error_code) > 128
        ):
            raise ValueError("media job error code is invalid")
        object.__setattr__(self, "output_path", _relative_path(self.output_path))
        object.__setattr__(self, "request_spec", _freeze_json(self.request_spec))
        object.__setattr__(self, "created_at", _aware(self.created_at))
        object.__setattr__(self, "updated_at", _aware(self.updated_at))

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID | None,
        workspace_id: UUID,
        conversation_id: UUID,
        task_id: UUID,
        version_id: UUID,
        turn_id: UUID | None,
        command_run_id: UUID,
        scope_digest: str,
        kind: MediaGenerationKind,
        model_id: str,
        endpoint_kind: ModelEndpointKind,
        output_path: str,
        request_spec: Mapping[str, Any],
        request_fingerprint: str,
        idempotency_key: str,
    ) -> MediaGenerationJob:
        return cls(
            id=new_id(),
            project_id=project_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            task_id=task_id,
            version_id=version_id,
            turn_id=turn_id,
            command_run_id=command_run_id,
            scope_digest=scope_digest,
            kind=kind,
            model_id=model_id,
            endpoint_kind=endpoint_kind,
            output_path=output_path,
            request_spec=request_spec,
            request_fingerprint=request_fingerprint,
            idempotency_key=idempotency_key,
            artifact_id=new_id(),
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def begin(self) -> None:
        self._transition(MediaGenerationStatus.GENERATING, progress=max(1, self.progress))

    def bind_video(self, *, provider_job_id: str, status: MediaGenerationStatus) -> None:
        if self.kind is not MediaGenerationKind.VIDEO:
            raise InvalidTransitionError("only video jobs bind provider job ids")
        if self.provider_job_id is not None and self.provider_job_id != provider_job_id:
            raise InvalidTransitionError("media provider job identity is immutable")
        if status not in {MediaGenerationStatus.PENDING, MediaGenerationStatus.IN_PROGRESS}:
            raise ValueError("video start must return pending or in-progress")
        self.provider_job_id = provider_job_id.strip()
        self._transition(status, progress=5 if status is MediaGenerationStatus.PENDING else 25)

    def update_video(self, *, status: MediaGenerationStatus, progress: int) -> None:
        if self.kind is not MediaGenerationKind.VIDEO or self.provider_job_id is None:
            raise InvalidTransitionError("video status requires a provider job")
        if status not in {
            MediaGenerationStatus.PENDING,
            MediaGenerationStatus.IN_PROGRESS,
            MediaGenerationStatus.FAILED,
            MediaGenerationStatus.CANCELLED,
        }:
            raise ValueError("unsupported video provider status")
        if status is self.status:
            if progress < self.progress:
                raise InvalidTransitionError("media progress cannot move backwards")
            self.progress = progress
            self._touch()
            return
        self._transition(status, progress=progress)

    def complete(self, *, usage_cost: str | None = None) -> None:
        self.usage_cost = _optional_cost(usage_cost)
        self.error_code = None
        self._transition(MediaGenerationStatus.COMPLETED, progress=100)

    def fail(self, error_code: str, *, usage_cost: str | None = None) -> None:
        self.error_code = _required_text(error_code, "error_code", 128)
        self.usage_cost = _optional_cost(usage_cost)
        self._transition(MediaGenerationStatus.FAILED, progress=self.progress)

    def cancel(self) -> None:
        self.error_code = None
        self._transition(MediaGenerationStatus.CANCELLED, progress=self.progress)

    def interrupt(self) -> None:
        self.error_code = "WORKER_INTERRUPTED"
        self._transition(MediaGenerationStatus.INTERRUPTED, progress=self.progress)

    def _transition(self, target: MediaGenerationStatus, *, progress: int) -> None:
        if target not in _TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"cannot transition media job from {self.status.value} to {target.value}"
            )
        if not self.progress <= progress <= 100:
            raise InvalidTransitionError("media progress cannot move backwards")
        self.status = target
        self.progress = progress
        self._touch()

    def _touch(self) -> None:
        self.revision += 1
        self.updated_at = datetime.now(UTC)


def _relative_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    parts = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or ":" in normalized
        or any(part in {"", ".", ".."} for part in parts)
        or len(normalized) > 1_024
    ):
        raise ValueError("media output path must be Workspace-relative")
    return normalized


def _freeze_json(value: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        normalized = json.loads(
            json.dumps(
                dict(value),
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    except (TypeError, ValueError) as error:
        raise ValueError("media request spec must contain canonical JSON") from error
    return MappingProxyType(normalized)


def _optional_cost(value: str | None) -> str | None:
    if value is None:
        return None
    return _required_text(value, "usage_cost", 64)


def _required_text(value: str, name: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"media job {name} is invalid")
    return normalized


def _is_sha256(value: str) -> bool:
    return all(character in "0123456789abcdef" for character in value)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("media job timestamps must be timezone-aware")
    return value.astimezone(UTC)


__all__ = [
    "MediaGenerationJob",
    "MediaGenerationKind",
    "MediaGenerationStatus",
]
