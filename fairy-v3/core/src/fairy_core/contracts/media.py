from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from fairy_core.contracts.common import ContractModel
from fairy_core.media.models import MediaGenerationKind, MediaGenerationStatus
from fairy_core.model_catalog.models import ModelEndpointKind


class MediaImageGenerateInput(ContractModel):
    task_id: UUID
    prompt: str = Field(min_length=1, max_length=20_000)
    output_path: str | None = Field(default=None, min_length=1, max_length=1_024)
    size: Literal["1024x1024"] = "1024x1024"
    aspect_ratio: Literal["1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16"] = "1:1"
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)
    idempotency_key: str = Field(min_length=1, max_length=512)
    user_confirmed: bool = True

    @field_validator("output_path")
    @classmethod
    def validate_output_path(cls, value: str | None) -> str | None:
        return _output_path(value, suffixes=(".png", ".webp", ".jpg", ".jpeg"))


class MediaAudioGenerateInput(ContractModel):
    task_id: UUID
    prompt: str = Field(min_length=1, max_length=20_000)
    output_path: str | None = Field(default=None, min_length=1, max_length=1_024)
    output_format: Literal["wav"] = "wav"
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)
    idempotency_key: str = Field(min_length=1, max_length=512)
    user_confirmed: bool

    @field_validator("output_path")
    @classmethod
    def validate_output_path(cls, value: str | None) -> str | None:
        return _output_path(value, suffixes=(".wav",))


class MediaVideoStartInput(ContractModel):
    task_id: UUID
    prompt: str = Field(min_length=1, max_length=20_000)
    output_path: str | None = Field(default=None, min_length=1, max_length=1_024)
    duration_seconds: int = Field(default=5, ge=1, le=20)
    resolution: Literal["720p", "1080p"] = "720p"
    aspect_ratio: Literal["1:1", "16:9", "9:16"] = "16:9"
    generate_audio: bool = True
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)
    idempotency_key: str = Field(min_length=1, max_length=512)
    user_confirmed: bool

    @field_validator("output_path")
    @classmethod
    def validate_output_path(cls, value: str | None) -> str | None:
        return _output_path(value, suffixes=(".mp4", ".webm"))


class MediaVideoJobInput(ContractModel):
    job_id: UUID


class MediaJobListInput(ContractModel):
    task_id: UUID


class MediaVideoCancelInput(MediaVideoJobInput):
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=512)
    user_confirmed: bool


class MediaGenerationJobModel(ContractModel):
    id: UUID
    project_id: UUID | None
    workspace_id: UUID
    conversation_id: UUID
    task_id: UUID
    version_id: UUID
    turn_id: UUID | None
    kind: MediaGenerationKind
    model_id: str
    endpoint_kind: ModelEndpointKind
    output_path: str
    status: MediaGenerationStatus
    progress: int = Field(ge=0, le=100)
    artifact_ids: tuple[UUID, ...]
    usage_cost: str | None
    error_code: str | None
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class MediaGenerationJobPageModel(ContractModel):
    items: tuple[MediaGenerationJobModel, ...]


def _output_path(value: str | None, *, suffixes: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    normalized = value.strip().replace("\\", "/")
    parts = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or ":" in normalized
        or any(part in {"", ".", ".."} for part in parts)
        or not normalized.casefold().endswith(suffixes)
    ):
        raise ValueError("output_path must be a safe Workspace-relative media path")
    return normalized


__all__ = [
    "MediaAudioGenerateInput",
    "MediaGenerationJobModel",
    "MediaGenerationJobPageModel",
    "MediaImageGenerateInput",
    "MediaJobListInput",
    "MediaVideoCancelInput",
    "MediaVideoJobInput",
    "MediaVideoStartInput",
]
