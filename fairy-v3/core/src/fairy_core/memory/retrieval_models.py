from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from fairy_core.domain.ids import new_id
from fairy_core.memory.models import MemoryAuthority, MemoryNamespace

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _now() -> datetime:
    return datetime.now(UTC)


class MemorySourceKind(StrEnum):
    CLAIM_REVISION = "claim_revision"
    OBSERVATION = "observation"
    DOMAIN_EVENT = "domain_event"


class MemorySnapshotStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"


class ProjectionState(StrEnum):
    READY = "ready"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class MemorySelectionReason(StrEnum):
    EXACT_CANONICAL = "exact_canonical"
    CANONICAL = "canonical"
    EXACT_PROFILE = "exact_profile"
    USER_PROFILE = "user_profile"
    CONVERSATION_DRAFT = "conversation_draft"
    LEXICAL_HISTORY = "lexical_history"
    RELATIONAL_FALLBACK = "relational_fallback"
    CONFLICT_DISCLOSURE = "conflict_disclosure"


@dataclass(frozen=True, slots=True)
class MemorySearchDocument:
    id: UUID
    source_kind: MemorySourceKind
    source_id: UUID
    source_revision: int | None
    namespace: MemoryNamespace | None
    project_id: UUID | None
    conversation_id: UUID | None
    task_id: UUID | None
    version_id: UUID | None
    language: str
    normalized_text: str
    content_hash: str
    source_cursor: int
    projection_generation: int
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.source_revision is not None and self.source_revision < 1:
            raise ValueError("source_revision must be positive")
        if self.source_kind is MemorySourceKind.CLAIM_REVISION and self.source_revision is None:
            raise ValueError("Claim revision documents require source_revision")
        language = self.language.strip().lower()
        if not language or len(language) > 32:
            raise ValueError("language is required and cannot exceed 32 characters")
        normalized_text = self.normalized_text.strip()
        if not normalized_text:
            raise ValueError("normalized_text is required")
        _validate_sha256(self.content_hash, "content_hash")
        expected_hash = _content_hash(normalized_text)
        if self.content_hash != expected_hash:
            raise ValueError("content_hash does not match normalized_text")
        if self.source_cursor < 1:
            raise ValueError("source_cursor must be positive")
        if self.projection_generation < 1:
            raise ValueError("projection_generation must be positive")
        _validate_aware_datetime(self.updated_at, "updated_at")
        _validate_namespace_scope(
            namespace=self.namespace,
            project_id=self.project_id,
            conversation_id=self.conversation_id,
        )
        object.__setattr__(self, "language", language)
        object.__setattr__(self, "normalized_text", normalized_text)

    @classmethod
    def create(
        cls,
        *,
        source_kind: MemorySourceKind,
        source_id: UUID,
        source_revision: int | None,
        namespace: MemoryNamespace | None,
        project_id: UUID | None,
        conversation_id: UUID | None,
        task_id: UUID | None,
        version_id: UUID | None,
        language: str,
        normalized_text: str,
        source_cursor: int,
        projection_generation: int,
    ) -> MemorySearchDocument:
        normalized = normalized_text.strip()
        return cls(
            id=new_id(),
            source_kind=source_kind,
            source_id=source_id,
            source_revision=source_revision,
            namespace=namespace,
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            version_id=version_id,
            language=language,
            normalized_text=normalized,
            content_hash=_content_hash(normalized),
            source_cursor=source_cursor,
            projection_generation=projection_generation,
            updated_at=_now(),
        )


@dataclass(frozen=True, slots=True)
class MemorySearchHit:
    document: MemorySearchDocument
    lexical_score: float
    exact_match: bool

    def __post_init__(self) -> None:
        _validate_score(self.lexical_score, "lexical_score")


@dataclass(frozen=True, slots=True)
class MemorySnapshotItem:
    ordinal: int
    source_kind: MemorySourceKind
    source_id: UUID
    source_revision: int | None
    namespace: MemoryNamespace | None
    selection_reason: MemorySelectionReason
    authority: MemoryAuthority
    score_components: Mapping[str, float] = field(repr=False)
    rendered_text: str
    rendered_text_hash: str
    token_count: int

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise ValueError("ordinal cannot be negative")
        if self.source_revision is not None and self.source_revision < 1:
            raise ValueError("source_revision must be positive")
        rendered_text = self.rendered_text.strip()
        if not rendered_text:
            raise ValueError("rendered_text is required")
        _validate_sha256(self.rendered_text_hash, "rendered_text_hash")
        if self.rendered_text_hash != _content_hash(rendered_text):
            raise ValueError("rendered_text_hash does not match rendered_text")
        if self.token_count < 1:
            raise ValueError("token_count must be positive")
        scores: dict[str, float] = {}
        for raw_name, raw_score in self.score_components.items():
            name = raw_name.strip()
            if not name:
                raise ValueError("score component names cannot be empty")
            score = float(raw_score)
            _validate_score(score, f"score component {name}")
            scores[name] = score
        object.__setattr__(self, "rendered_text", rendered_text)
        object.__setattr__(self, "score_components", MappingProxyType(scores))

    @classmethod
    def create(
        cls,
        *,
        ordinal: int,
        source_kind: MemorySourceKind,
        source_id: UUID,
        source_revision: int | None,
        namespace: MemoryNamespace | None,
        selection_reason: MemorySelectionReason,
        authority: MemoryAuthority,
        score_components: Mapping[str, float],
        rendered_text: str,
        token_count: int,
    ) -> MemorySnapshotItem:
        rendered = rendered_text.strip()
        return cls(
            ordinal=ordinal,
            source_kind=source_kind,
            source_id=source_id,
            source_revision=source_revision,
            namespace=namespace,
            selection_reason=selection_reason,
            authority=authority,
            score_components=score_components,
            rendered_text=rendered,
            rendered_text_hash=_content_hash(rendered),
            token_count=token_count,
        )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "source_kind": self.source_kind.value,
            "source_id": str(self.source_id),
            "source_revision": self.source_revision,
            "namespace": self.namespace.value if self.namespace is not None else None,
            "selection_reason": self.selection_reason.value,
            "authority": self.authority.value,
            "score_components": dict(self.score_components),
            "rendered_text_hash": self.rendered_text_hash,
            "token_count": self.token_count,
        }


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    base_version_id: UUID | None
    target_version_id: UUID | None
    snapshot_version: int
    policy_version: str
    source_watermark_cursor: int
    projection_generation: int
    projection_watermark_cursor: int
    projection_state: ProjectionState
    status: MemorySnapshotStatus
    degraded_reason: str | None
    content_hash: str
    token_count: int
    items: tuple[MemorySnapshotItem, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if self.snapshot_version != 1:
            raise ValueError("snapshot_version must be 1")
        if not self.policy_version.strip():
            raise ValueError("policy_version is required")
        if self.source_watermark_cursor < 0:
            raise ValueError("source_watermark_cursor cannot be negative")
        if self.projection_generation < 1:
            raise ValueError("projection_generation must be positive")
        if self.projection_watermark_cursor < 0:
            raise ValueError("projection_watermark_cursor cannot be negative")
        _validate_snapshot_status(
            status=self.status,
            projection_state=self.projection_state,
            source_watermark_cursor=self.source_watermark_cursor,
            projection_watermark_cursor=self.projection_watermark_cursor,
            degraded_reason=self.degraded_reason,
        )
        if tuple(item.ordinal for item in self.items) != tuple(range(len(self.items))):
            raise ValueError("Snapshot item ordinals must be contiguous from zero")
        expected_tokens = sum(item.token_count for item in self.items)
        if self.token_count != expected_tokens:
            raise ValueError("token_count does not match Snapshot items")
        if self.token_count > 3_000:
            raise ValueError("Snapshot cannot exceed the 3,000 token hard ceiling")
        _validate_sha256(self.content_hash, "content_hash")
        expected_hash = _snapshot_content_hash(
            snapshot_version=self.snapshot_version,
            policy_version=self.policy_version,
            source_watermark_cursor=self.source_watermark_cursor,
            projection_generation=self.projection_generation,
            projection_watermark_cursor=self.projection_watermark_cursor,
            projection_state=self.projection_state,
            status=self.status,
            degraded_reason=self.degraded_reason,
            items=self.items,
        )
        if self.content_hash != expected_hash:
            raise ValueError("content_hash does not match Snapshot content")
        _validate_aware_datetime(self.created_at, "created_at")

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID | None,
        conversation_id: UUID,
        task_id: UUID,
        base_version_id: UUID | None,
        target_version_id: UUID | None,
        policy_version: str,
        source_watermark_cursor: int,
        projection_generation: int,
        projection_watermark_cursor: int,
        projection_state: ProjectionState,
        status: MemorySnapshotStatus,
        degraded_reason: str | None,
        items: tuple[MemorySnapshotItem, ...],
    ) -> MemorySnapshot:
        snapshot_version = 1
        normalized_policy = policy_version.strip()
        content_hash = _snapshot_content_hash(
            snapshot_version=snapshot_version,
            policy_version=normalized_policy,
            source_watermark_cursor=source_watermark_cursor,
            projection_generation=projection_generation,
            projection_watermark_cursor=projection_watermark_cursor,
            projection_state=projection_state,
            status=status,
            degraded_reason=degraded_reason,
            items=items,
        )
        return cls(
            id=new_id(),
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            base_version_id=base_version_id,
            target_version_id=target_version_id,
            snapshot_version=snapshot_version,
            policy_version=normalized_policy,
            source_watermark_cursor=source_watermark_cursor,
            projection_generation=projection_generation,
            projection_watermark_cursor=projection_watermark_cursor,
            projection_state=projection_state,
            status=status,
            degraded_reason=degraded_reason,
            content_hash=content_hash,
            token_count=sum(item.token_count for item in items),
            items=items,
            created_at=_now(),
        )

    @classmethod
    def restore(cls, **values: object) -> MemorySnapshot:
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class MemoryProjectionHealth:
    generation: int
    state: ProjectionState
    source_watermark_cursor: int
    projected_watermark_cursor: int
    last_error_code: str | None
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ValueError("generation must be positive")
        if self.source_watermark_cursor < 0 or self.projected_watermark_cursor < 0:
            raise ValueError("projection watermarks cannot be negative")
        if (
            self.state is ProjectionState.READY
            and self.projected_watermark_cursor < self.source_watermark_cursor
        ):
            raise ValueError("READY projection cannot trail its source watermark")
        if self.state is ProjectionState.READY and self.last_error_code is not None:
            raise ValueError("READY projection cannot carry an error code")
        if self.last_error_code is not None and not self.last_error_code.strip():
            raise ValueError("last_error_code cannot be blank")
        _validate_aware_datetime(self.updated_at, "updated_at")


def _snapshot_content_hash(
    *,
    snapshot_version: int,
    policy_version: str,
    source_watermark_cursor: int,
    projection_generation: int,
    projection_watermark_cursor: int,
    projection_state: ProjectionState,
    status: MemorySnapshotStatus,
    degraded_reason: str | None,
    items: tuple[MemorySnapshotItem, ...],
) -> str:
    payload = {
        "snapshot_version": snapshot_version,
        "policy_version": policy_version,
        "source_watermark_cursor": source_watermark_cursor,
        "projection_generation": projection_generation,
        "projection_watermark_cursor": projection_watermark_cursor,
        "projection_state": projection_state.value,
        "status": status.value,
        "degraded_reason": degraded_reason,
        "items": [item.canonical_payload() for item in items],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="strict")).hexdigest()


def _validate_sha256(value: str, name: str) -> None:
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _validate_score(value: float, name: str) -> None:
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} score must be finite and between 0 and 1")


def _validate_aware_datetime(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _validate_namespace_scope(
    *,
    namespace: MemoryNamespace | None,
    project_id: UUID | None,
    conversation_id: UUID | None,
) -> None:
    if namespace is MemoryNamespace.PROJECT_CANONICAL and project_id is None:
        raise ValueError("project_canonical document requires project_id")
    if namespace is MemoryNamespace.CONVERSATION_DRAFT and conversation_id is None:
        raise ValueError("conversation_draft document requires conversation_id")


def _validate_snapshot_status(
    *,
    status: MemorySnapshotStatus,
    projection_state: ProjectionState,
    source_watermark_cursor: int,
    projection_watermark_cursor: int,
    degraded_reason: str | None,
) -> None:
    if status is MemorySnapshotStatus.READY:
        if projection_state is not ProjectionState.READY:
            raise ValueError("READY Snapshot requires a READY projection")
        if projection_watermark_cursor < source_watermark_cursor:
            raise ValueError("READY Snapshot projection cannot trail its source watermark")
        if degraded_reason is not None:
            raise ValueError("READY Snapshot cannot have a degraded reason")
        return
    if projection_state is ProjectionState.READY:
        raise ValueError("DEGRADED Snapshot cannot report a READY projection")
    if not (degraded_reason or "").strip():
        raise ValueError("DEGRADED Snapshot requires a degraded reason")


__all__ = [
    "MemoryProjectionHealth",
    "MemorySearchDocument",
    "MemorySearchHit",
    "MemorySelectionReason",
    "MemorySnapshot",
    "MemorySnapshotItem",
    "MemorySnapshotStatus",
    "MemorySourceKind",
    "ProjectionState",
]
