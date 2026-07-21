from __future__ import annotations

import hashlib
import math
from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from fairy_core.contracts.common import ContractModel
from fairy_core.memory.models import MemoryAuthority, MemoryNamespace
from fairy_core.memory.retrieval_models import (
    MemorySelectionReason,
    MemorySnapshotStatus,
    MemorySourceKind,
    ProjectionState,
)


class MemorySearchDocumentModel(ContractModel):
    id: UUID
    source_kind: MemorySourceKind
    source_id: UUID
    source_revision: int | None = Field(default=None, ge=1)
    namespace: MemoryNamespace | None
    project_id: UUID | None
    conversation_id: UUID | None
    task_id: UUID | None
    version_id: UUID | None
    language: str = Field(min_length=1, max_length=32)
    normalized_text: str = Field(min_length=1, max_length=100_000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_cursor: int = Field(ge=1)
    projection_generation: int = Field(ge=1)
    updated_at: datetime

    @model_validator(mode="after")
    def require_consistent_source(self) -> MemorySearchDocumentModel:
        expected_hash = hashlib.sha256(self.normalized_text.encode("utf-8")).hexdigest()
        if self.content_hash != expected_hash:
            raise ValueError("content_hash does not match normalized_text")
        if self.source_kind is MemorySourceKind.CLAIM_REVISION and self.source_revision is None:
            raise ValueError("Claim revision document requires source_revision")
        if self.namespace is MemoryNamespace.PROJECT_CANONICAL and self.project_id is None:
            raise ValueError("project_canonical document requires project_id")
        if self.namespace is MemoryNamespace.CONVERSATION_DRAFT and self.conversation_id is None:
            raise ValueError("conversation_draft document requires conversation_id")
        return self


class MemorySearchHitModel(ContractModel):
    document: MemorySearchDocumentModel
    lexical_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    exact_match: bool


class MemorySearchPageModel(ContractModel):
    items: tuple[MemorySearchHitModel, ...]


class MemorySnapshotItemModel(ContractModel):
    ordinal: int = Field(ge=0)
    source_kind: MemorySourceKind
    source_id: UUID
    source_revision: int | None = Field(default=None, ge=1)
    namespace: MemoryNamespace | None
    selection_reason: MemorySelectionReason
    authority: MemoryAuthority
    score_components: dict[str, float]
    rendered_text: str = Field(min_length=1, max_length=100_000)
    rendered_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    token_count: int = Field(ge=1, le=3_000)

    @field_validator("score_components")
    @classmethod
    def require_finite_score_components(
        cls,
        value: dict[str, float],
    ) -> dict[str, float]:
        for name, score in value.items():
            if not name.strip() or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("score components must be named, finite, and between 0 and 1")
        return value

    @model_validator(mode="after")
    def require_rendered_text_hash(self) -> MemorySnapshotItemModel:
        expected_hash = hashlib.sha256(self.rendered_text.encode("utf-8")).hexdigest()
        if self.rendered_text_hash != expected_hash:
            raise ValueError("rendered_text_hash does not match rendered_text")
        return self


class MemorySnapshotModel(ContractModel):
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    base_version_id: UUID | None
    target_version_id: UUID | None
    snapshot_version: int = Field(ge=1)
    policy_version: str = Field(min_length=1, max_length=128)
    source_watermark_cursor: int = Field(ge=0)
    projection_generation: int = Field(ge=1)
    projection_watermark_cursor: int = Field(ge=0)
    projection_state: ProjectionState
    status: MemorySnapshotStatus
    degraded_reason: str | None = Field(default=None, min_length=1, max_length=128)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    token_count: int = Field(ge=0, le=3_000)
    items: tuple[MemorySnapshotItemModel, ...]
    created_at: datetime

    @model_validator(mode="after")
    def require_consistent_manifest(self) -> MemorySnapshotModel:
        if tuple(item.ordinal for item in self.items) != tuple(range(len(self.items))):
            raise ValueError("Snapshot item ordinals must be contiguous from zero")
        if self.token_count != sum(item.token_count for item in self.items):
            raise ValueError("Snapshot token_count must equal item token counts")
        if self.status is MemorySnapshotStatus.READY:
            if self.projection_state is not ProjectionState.READY:
                raise ValueError("READY Snapshot requires READY projection state")
            if self.projection_watermark_cursor < self.source_watermark_cursor:
                raise ValueError("READY Snapshot projection cannot trail source")
            if self.degraded_reason is not None:
                raise ValueError("READY Snapshot cannot have a degraded reason")
        else:
            if self.projection_state is ProjectionState.READY:
                raise ValueError("DEGRADED Snapshot cannot report READY projection state")
            if self.degraded_reason is None:
                raise ValueError("DEGRADED Snapshot requires a degraded reason")
        return self


class MemoryProjectionHealthModel(ContractModel):
    generation: int = Field(ge=1)
    state: ProjectionState
    source_watermark_cursor: int = Field(ge=0)
    projected_watermark_cursor: int = Field(ge=0)
    lag: int = Field(ge=0)
    last_error_code: str | None = Field(default=None, min_length=1, max_length=128)
    updated_at: datetime

    @model_validator(mode="after")
    def require_consistent_ready_state(self) -> MemoryProjectionHealthModel:
        expected_lag = max(
            0,
            self.source_watermark_cursor - self.projected_watermark_cursor,
        )
        if self.lag != expected_lag:
            raise ValueError("projection lag does not match its watermarks")
        if self.state is ProjectionState.READY:
            if self.projected_watermark_cursor < self.source_watermark_cursor:
                raise ValueError("READY projection cannot trail source")
            if self.last_error_code is not None:
                raise ValueError("READY projection cannot carry an error code")
        return self


__all__ = [
    "MemoryProjectionHealthModel",
    "MemorySearchDocumentModel",
    "MemorySearchHitModel",
    "MemorySearchPageModel",
    "MemorySnapshotItemModel",
    "MemorySnapshotModel",
]
