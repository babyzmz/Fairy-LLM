from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from fairy_core.providers import CancellationToken


class MediaProviderVideoStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class ImageGenerationRequest:
    model_id: str
    prompt: str
    size: str
    aspect_ratio: str
    seed: int | None
    idempotency_key: str
    zero_data_retention: bool


@dataclass(frozen=True, slots=True)
class MusicGenerationRequest:
    model_id: str
    prompt: str
    output_format: str
    seed: int | None
    idempotency_key: str
    zero_data_retention: bool


@dataclass(frozen=True, slots=True)
class VideoGenerationRequest:
    model_id: str
    prompt: str
    duration_seconds: int
    resolution: str
    aspect_ratio: str
    generate_audio: bool
    seed: int | None
    idempotency_key: str
    zero_data_retention: bool


@dataclass(frozen=True, slots=True)
class GeneratedMedia:
    content: bytes
    media_type: str
    usage_cost: str | None = None
    transcript: str | None = None

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError("generated media content is empty")
        if len(self.content) > 512 * 1024 * 1024:
            raise ValueError("generated media exceeds the 512 MiB transfer limit")
        if "/" not in self.media_type or len(self.media_type) > 255:
            raise ValueError("generated media type is invalid")


@dataclass(frozen=True, slots=True)
class VideoProviderJob:
    provider_job_id: str
    status: MediaProviderVideoStatus
    usage_cost: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not self.provider_job_id.strip() or len(self.provider_job_id) > 255:
            raise ValueError("video provider job id is invalid")


class MediaProvider(Protocol):
    @property
    def account_id(self) -> str: ...

    @property
    def credential_configured(self) -> bool: ...

    def close(self) -> None: ...

    def generate_image(
        self,
        request: ImageGenerationRequest,
        cancellation: CancellationToken,
    ) -> GeneratedMedia: ...

    def generate_music(
        self,
        request: MusicGenerationRequest,
        cancellation: CancellationToken,
    ) -> GeneratedMedia: ...

    def start_video(
        self,
        request: VideoGenerationRequest,
        cancellation: CancellationToken,
    ) -> VideoProviderJob: ...

    def get_video(
        self,
        provider_job_id: str,
        cancellation: CancellationToken,
    ) -> VideoProviderJob: ...

    def download_video(
        self,
        provider_job_id: str,
        cancellation: CancellationToken,
    ) -> GeneratedMedia: ...


__all__ = [
    "GeneratedMedia",
    "ImageGenerationRequest",
    "MediaProvider",
    "MediaProviderVideoStatus",
    "MusicGenerationRequest",
    "VideoGenerationRequest",
    "VideoProviderJob",
]
