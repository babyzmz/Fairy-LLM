from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID

from fairy_core.domain.ids import new_id


class PresentationFidelity(StrEnum):
    NATIVE = "native"
    NORMALIZED_HIGH = "normalized_high"
    APPROXIMATE = "approximate"
    CONTENT_ONLY = "content_only"
    UNSUPPORTED = "unsupported"


class RenderJobStatus(StrEnum):
    PROBING = "probing"
    WAITING_FOR_PACK = "waiting_for_pack"
    QUEUED = "queued"
    CONVERTING = "converting"
    VALIDATING = "validating"
    READY = "ready"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELED = "canceled"
    QUARANTINED = "quarantined"


_TRANSITIONS: dict[RenderJobStatus, frozenset[RenderJobStatus]] = {
    RenderJobStatus.PROBING: frozenset(
        {
            RenderJobStatus.WAITING_FOR_PACK,
            RenderJobStatus.QUEUED,
            RenderJobStatus.READY,
            RenderJobStatus.FAILED,
            RenderJobStatus.CANCELED,
            RenderJobStatus.QUARANTINED,
        }
    ),
    RenderJobStatus.WAITING_FOR_PACK: frozenset(
        {RenderJobStatus.QUEUED, RenderJobStatus.CANCELED, RenderJobStatus.QUARANTINED}
    ),
    RenderJobStatus.QUEUED: frozenset(
        {RenderJobStatus.CONVERTING, RenderJobStatus.CANCELED, RenderJobStatus.FAILED}
    ),
    RenderJobStatus.CONVERTING: frozenset(
        {RenderJobStatus.VALIDATING, RenderJobStatus.CANCELED, RenderJobStatus.FAILED}
    ),
    RenderJobStatus.VALIDATING: frozenset(
        {
            RenderJobStatus.READY,
            RenderJobStatus.PARTIAL,
            RenderJobStatus.FAILED,
            RenderJobStatus.QUARANTINED,
        }
    ),
    RenderJobStatus.READY: frozenset(),
    RenderJobStatus.PARTIAL: frozenset(),
    RenderJobStatus.FAILED: frozenset(),
    RenderJobStatus.CANCELED: frozenset(),
    RenderJobStatus.QUARANTINED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class FileRenderJob:
    id: UUID
    workspace_id: UUID
    version_id: UUID
    file_set_id: UUID
    source_path: str
    source_hash: str
    cache_key: str
    requested_mode: str
    renderer_pack_id: str | None
    renderer_pack_version: str | None
    status: RenderJobStatus = RenderJobStatus.PROBING
    progress: int = 0
    error_code: str | None = None
    public_summary: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        *,
        workspace_id: UUID,
        version_id: UUID,
        file_set_id: UUID,
        source_path: str,
        source_hash: str,
        cache_key: str,
        requested_mode: str,
        renderer_pack_id: str | None = None,
        renderer_pack_version: str | None = None,
    ) -> FileRenderJob:
        return cls(
            id=new_id(),
            workspace_id=workspace_id,
            version_id=version_id,
            file_set_id=file_set_id,
            source_path=source_path,
            source_hash=source_hash,
            cache_key=cache_key,
            requested_mode=requested_mode,
            renderer_pack_id=renderer_pack_id,
            renderer_pack_version=renderer_pack_version,
        )

    def transition(
        self,
        status: RenderJobStatus,
        *,
        progress: int | None = None,
        error_code: str | None = None,
        public_summary: str | None = None,
    ) -> FileRenderJob:
        if status not in _TRANSITIONS[self.status]:
            raise ValueError(f"invalid render job transition: {self.status} -> {status}")
        next_progress = self.progress if progress is None else progress
        if not 0 <= next_progress <= 100:
            raise ValueError("render progress must be between 0 and 100")
        if status in {RenderJobStatus.READY, RenderJobStatus.PARTIAL}:
            next_progress = 100
        if status == RenderJobStatus.FAILED and not error_code:
            raise ValueError("failed render jobs require an error code")
        return replace(
            self,
            status=status,
            progress=next_progress,
            error_code=error_code,
            public_summary=public_summary,
            updated_at=datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class DerivedAsset:
    id: UUID
    presentation_id: UUID
    role: str
    media_type: str
    content_hash: str
    byte_length: int
    storage_key: str
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class FilePresentation:
    id: UUID
    job_id: UUID
    workspace_id: UUID
    version_id: UUID
    file_set_id: UUID
    source_path: str
    source_hash: str
    renderer: str
    fidelity: PresentationFidelity
    status: RenderJobStatus
    capabilities: tuple[str, ...]
    assets: tuple[DerivedAsset, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


__all__ = [
    "DerivedAsset",
    "FilePresentation",
    "FileRenderJob",
    "PresentationFidelity",
    "RenderJobStatus",
]
