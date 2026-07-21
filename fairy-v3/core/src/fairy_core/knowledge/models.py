from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID

from fairy_core.domain.ids import new_id
from fairy_core.knowledge.tool_snapshot import ToolDefinitionSnapshot

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _now() -> datetime:
    return datetime.now(UTC)


class KnowledgeSourceKind(StrEnum):
    OBSIDIAN = "obsidian"
    MANAGED_DOCUMENT = "managed_document"


class KnowledgeSourceStatus(StrEnum):
    CONFIGURED = "configured"
    SYNCING = "syncing"
    READY = "ready"
    PARTIAL = "partial"
    FAILED = "failed"
    DISABLED = "disabled"


class KnowledgeSyncStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class KnowledgeSnapshotStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class KnowledgeIngestItem:
    relative_path: str
    title: str
    kind: str
    content: str
    content_hash: str
    links: tuple[str, ...]
    frontmatter: Mapping[str, Any]
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        _required(self.relative_path, "relative_path", 1024)
        _required(self.title, "title", 200)
        _required(self.kind, "kind", 64)
        _digest(self.content_hash, "content_hash")
        if self.content_hash != hashlib.sha256(self.content.encode("utf-8")).hexdigest():
            raise ValueError("Knowledge ingest hash does not match content")
        object.__setattr__(
            self,
            "frontmatter",
            MappingProxyType(_canonical_mapping(self.frontmatter)),
        )
        object.__setattr__(
            self,
            "provenance",
            MappingProxyType(_canonical_mapping(self.provenance)),
        )


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    id: UUID
    project_id: UUID
    kind: KnowledgeSourceKind
    device_id: str
    display_name: str
    display_path: str
    status: KnowledgeSourceStatus
    revision: int
    sync_cursor: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _required(self.device_id, "device_id", 255)
        _required(self.display_name, "display_name", 200)
        _required(self.display_path, "display_path", 1024)
        if self.revision < 1 or self.sync_cursor < 0:
            raise ValueError("Knowledge Source revisions must be positive")
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")

    @classmethod
    def create(
        cls,
        *,
        source_id: UUID,
        project_id: UUID,
        kind: KnowledgeSourceKind,
        device_id: str,
        display_name: str,
        display_path: str,
        status: KnowledgeSourceStatus = KnowledgeSourceStatus.CONFIGURED,
        revision: int = 1,
        sync_cursor: int = 0,
    ) -> KnowledgeSource:
        now = _now()
        return cls(
            id=source_id,
            project_id=project_id,
            kind=kind,
            device_id=device_id.strip(),
            display_name=display_name.strip(),
            display_path=display_path.strip(),
            status=status,
            revision=revision,
            sync_cursor=sync_cursor,
            created_at=now,
            updated_at=now,
        )


@dataclass(frozen=True, slots=True)
class KnowledgeCollection:
    id: UUID
    source_id: UUID
    project_id: UUID
    read_scope: str
    allowed_directories: tuple[str, ...]
    filters: Mapping[str, Any]
    managed_directory: str
    scope_kind: str
    revision: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.read_scope not in {"selected_directories", "whole_vault"}:
            raise ValueError("Knowledge Collection read_scope is invalid")
        if self.read_scope == "selected_directories" and not self.allowed_directories:
            raise ValueError("Selected-directory Knowledge Collection cannot be empty")
        if self.read_scope == "whole_vault" and self.allowed_directories:
            raise ValueError("Whole-Vault Knowledge Collection cannot list directories")
        _required(self.managed_directory, "managed_directory", 1024)
        _required(self.scope_kind, "scope_kind", 64)
        if self.revision < 1:
            raise ValueError("Knowledge Collection revision must be positive")
        object.__setattr__(self, "filters", MappingProxyType(_canonical_mapping(self.filters)))
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")

    @classmethod
    def create(
        cls,
        *,
        source_id: UUID,
        project_id: UUID,
        read_scope: str,
        allowed_directories: tuple[str, ...],
        managed_directory: str,
        filters: Mapping[str, Any] | None = None,
        scope_kind: str = "project",
    ) -> KnowledgeCollection:
        now = _now()
        return cls(
            id=new_id(),
            source_id=source_id,
            project_id=project_id,
            read_scope=read_scope,
            allowed_directories=tuple(allowed_directories),
            filters=filters or {"include": ["**/*.md", "**/*.canvas"]},
            managed_directory=managed_directory.strip(),
            scope_kind=scope_kind,
            revision=1,
            created_at=now,
            updated_at=now,
        )


@dataclass(frozen=True, slots=True)
class KnowledgeRevision:
    id: UUID
    item_id: UUID
    source_id: UUID
    project_id: UUID
    revision: int
    relative_path: str
    title: str
    kind: str
    content: str
    content_hash: str
    revision_hash: str
    links: tuple[str, ...]
    frontmatter: Mapping[str, Any]
    provenance: Mapping[str, Any]
    source_cursor: int
    created_at: datetime

    def __post_init__(self) -> None:
        if self.revision < 1 or self.source_cursor < 1:
            raise ValueError("Knowledge Revision counters must be positive")
        _required(self.relative_path, "relative_path", 1024)
        _required(self.title, "title", 200)
        _required(self.kind, "kind", 64)
        _digest(self.content_hash, "content_hash")
        _digest(self.revision_hash, "revision_hash")
        if self.content_hash != hashlib.sha256(self.content.encode("utf-8")).hexdigest():
            raise ValueError("Knowledge Revision content hash does not match content")
        object.__setattr__(
            self,
            "frontmatter",
            MappingProxyType(_canonical_mapping(self.frontmatter)),
        )
        object.__setattr__(
            self,
            "provenance",
            MappingProxyType(_canonical_mapping(self.provenance)),
        )
        expected = _revision_hash(
            item_id=self.item_id,
            revision=self.revision,
            relative_path=self.relative_path,
            title=self.title,
            kind=self.kind,
            content_hash=self.content_hash,
            links=self.links,
            frontmatter=self.frontmatter,
            provenance=self.provenance,
            source_cursor=self.source_cursor,
        )
        if self.revision_hash != expected:
            raise ValueError("Knowledge Revision hash does not match its immutable content")
        _aware(self.created_at, "created_at")

    @classmethod
    def create(
        cls,
        *,
        item_id: UUID,
        source_id: UUID,
        project_id: UUID,
        revision: int,
        relative_path: str,
        title: str,
        kind: str,
        content: str,
        links: tuple[str, ...],
        frontmatter: Mapping[str, Any] | None,
        provenance: Mapping[str, Any],
        source_cursor: int,
    ) -> KnowledgeRevision:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        normalized_frontmatter = _canonical_mapping(frontmatter or {})
        normalized_provenance = _canonical_mapping(provenance)
        return cls(
            id=new_id(),
            item_id=item_id,
            source_id=source_id,
            project_id=project_id,
            revision=revision,
            relative_path=relative_path,
            title=title,
            kind=kind,
            content=content,
            content_hash=content_hash,
            revision_hash=_revision_hash(
                item_id=item_id,
                revision=revision,
                relative_path=relative_path,
                title=title,
                kind=kind,
                content_hash=content_hash,
                links=links,
                frontmatter=normalized_frontmatter,
                provenance=normalized_provenance,
                source_cursor=source_cursor,
            ),
            links=tuple(links),
            frontmatter=normalized_frontmatter,
            provenance=normalized_provenance,
            source_cursor=source_cursor,
            created_at=_now(),
        )


@dataclass(frozen=True, slots=True)
class KnowledgeItem:
    id: UUID
    source_id: UUID
    project_id: UUID
    relative_path: str
    current_revision_id: UUID | None
    current_revision: int
    tombstoned_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class KnowledgeSyncDelta:
    created_revisions: tuple[KnowledgeRevision, ...]
    deleted_item_ids: tuple[UUID, ...]

    @property
    def changed_count(self) -> int:
        return len(self.created_revisions)

    @property
    def deleted_count(self) -> int:
        return len(self.deleted_item_ids)


@dataclass(frozen=True, slots=True)
class KnowledgeSyncRun:
    id: UUID
    source_id: UUID
    project_id: UUID
    status: KnowledgeSyncStatus
    expected_source_revision: int
    request_fingerprint: str
    source_cursor: int
    scanned_count: int
    changed_count: int
    deleted_count: int
    failed_count: int
    error_code: str | None
    lease_owner: str | None
    lease_until: datetime | None
    lease_fence: int
    attempts: int
    cancellation_revision: int
    started_at: datetime
    completed_at: datetime | None

    def __post_init__(self) -> None:
        if self.expected_source_revision < 1:
            raise ValueError("Knowledge Sync expected revision must be positive")
        _digest(self.request_fingerprint, "request_fingerprint")
        if (
            min(
                self.source_cursor,
                self.scanned_count,
                self.changed_count,
                self.deleted_count,
                self.failed_count,
                self.lease_fence,
                self.attempts,
                self.cancellation_revision,
            )
            < 0
        ):
            raise ValueError("Knowledge Sync counters cannot be negative")
        _aware(self.started_at, "started_at")
        if self.lease_until is not None:
            _aware(self.lease_until, "lease_until")
        if self.completed_at is not None:
            _aware(self.completed_at, "completed_at")
        if (self.lease_owner is None) != (self.lease_until is None):
            raise ValueError("Knowledge Sync lease owner and deadline must be paired")
        if self.status is KnowledgeSyncStatus.RUNNING and self.lease_owner is None:
            raise ValueError("Running Knowledge Sync requires a lease")
        if self.status is not KnowledgeSyncStatus.RUNNING and self.lease_owner is not None:
            raise ValueError("Only a running Knowledge Sync may hold a lease")
        terminal = self.status in {
            KnowledgeSyncStatus.COMPLETED,
            KnowledgeSyncStatus.FAILED,
            KnowledgeSyncStatus.CANCELLED,
        }
        if terminal != (self.completed_at is not None):
            raise ValueError("Knowledge Sync terminal state and completion time do not match")

    @classmethod
    def enqueue(
        cls,
        *,
        source_id: UUID,
        project_id: UUID,
        expected_source_revision: int,
        request_fingerprint: str,
        source_cursor: int,
    ) -> KnowledgeSyncRun:
        return cls(
            id=new_id(),
            source_id=source_id,
            project_id=project_id,
            status=KnowledgeSyncStatus.QUEUED,
            expected_source_revision=expected_source_revision,
            request_fingerprint=request_fingerprint,
            source_cursor=source_cursor,
            scanned_count=0,
            changed_count=0,
            deleted_count=0,
            failed_count=0,
            error_code=None,
            lease_owner=None,
            lease_until=None,
            lease_fence=0,
            attempts=0,
            cancellation_revision=0,
            started_at=_now(),
            completed_at=None,
        )


@dataclass(frozen=True, slots=True)
class KnowledgeSnapshotItem:
    ordinal: int
    item_id: UUID
    revision_id: UUID
    source_id: UUID
    relative_path: str
    title: str
    content_hash: str
    revision_hash: str

    def canonical_payload(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "item_id": str(self.item_id),
            "revision_id": str(self.revision_id),
            "source_id": str(self.source_id),
            "relative_path": self.relative_path,
            "title": self.title,
            "content_hash": self.content_hash,
            "revision_hash": self.revision_hash,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeSnapshot:
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    source_cursor: int
    status: KnowledgeSnapshotStatus
    degraded_reason: str | None
    content_hash: str
    items: tuple[KnowledgeSnapshotItem, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if self.source_cursor < 0:
            raise ValueError("Knowledge Snapshot cursor cannot be negative")
        if tuple(item.ordinal for item in self.items) != tuple(range(len(self.items))):
            raise ValueError("Knowledge Snapshot ordinals must be contiguous")
        if self.status is KnowledgeSnapshotStatus.READY and self.degraded_reason is not None:
            raise ValueError("Ready Knowledge Snapshot cannot have a degraded reason")
        if self.status is KnowledgeSnapshotStatus.DEGRADED and not self.degraded_reason:
            raise ValueError("Degraded Knowledge Snapshot requires a reason")
        _digest(self.content_hash, "content_hash")
        if self.content_hash != _snapshot_hash(
            source_cursor=self.source_cursor,
            status=self.status,
            degraded_reason=self.degraded_reason,
            items=self.items,
        ):
            raise ValueError("Knowledge Snapshot hash does not match its immutable content")

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID | None,
        conversation_id: UUID,
        task_id: UUID,
        source_cursor: int,
        items: tuple[KnowledgeSnapshotItem, ...],
        status: KnowledgeSnapshotStatus = KnowledgeSnapshotStatus.READY,
        degraded_reason: str | None = None,
    ) -> KnowledgeSnapshot:
        return cls(
            id=new_id(),
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            source_cursor=source_cursor,
            status=status,
            degraded_reason=degraded_reason,
            content_hash=_snapshot_hash(
                source_cursor=source_cursor,
                status=status,
                degraded_reason=degraded_reason,
                items=items,
            ),
            items=items,
            created_at=_now(),
        )


@dataclass(frozen=True, slots=True)
class HarnessContextManifest:
    id: UUID
    task_id: UUID
    scope_digest: str
    workspace_id: UUID
    workspace_version_id: UUID | None
    memory_snapshot_id: UUID
    memory_snapshot_hash: str
    knowledge_snapshot_id: UUID
    knowledge_snapshot_hash: str
    tool_registry_generation: int
    tool_registry_digest: str
    tool_definitions: tuple[ToolDefinitionSnapshot, ...]
    skill_package_digests: tuple[str, ...]
    mcp_capability_snapshot: tuple[str, ...]
    model_selection: Mapping[str, Any]
    budget: Mapping[str, Any]
    content_hash: str
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        for name in (
            "scope_digest",
            "memory_snapshot_hash",
            "knowledge_snapshot_hash",
            "tool_registry_digest",
            "content_hash",
        ):
            _digest(getattr(self, name), name)
        if self.tool_registry_generation < 0:
            raise ValueError("Tool Registry generation cannot be negative")
        tool_names = tuple(snapshot.name for snapshot in self.tool_definitions)
        if tool_names != tuple(sorted(tool_names)) or len(tool_names) != len(set(tool_names)):
            raise ValueError("Harness Tool Definitions must be unique and sorted")
        if self.tool_definitions:
            captured_registry_digest = _hash(
                [
                    {
                        "name": snapshot.name,
                        "digest": snapshot.definition_digest,
                    }
                    for snapshot in self.tool_definitions
                ]
            )
            if captured_registry_digest != self.tool_registry_digest:
                raise ValueError("Harness Tool Definitions do not match the Registry digest")
        object.__setattr__(
            self,
            "model_selection",
            MappingProxyType(_canonical_mapping(self.model_selection)),
        )
        object.__setattr__(self, "budget", MappingProxyType(_canonical_mapping(self.budget)))
        if self.content_hash != _manifest_hash(self):
            raise ValueError("Harness Manifest hash does not match its immutable content")

    @classmethod
    def create(cls, **values: Any) -> HarnessContextManifest:
        values["id"] = values.get("id") or new_id()
        values["created_at"] = values.get("created_at") or _now()
        values["content_hash"] = _hash(_manifest_payload(values))
        return cls(**values)


def _revision_hash(**values: Any) -> str:
    return _hash(
        {
            "version": 1,
            "item_id": str(values["item_id"]),
            "revision": values["revision"],
            "relative_path": values["relative_path"],
            "title": values["title"],
            "kind": values["kind"],
            "content_hash": values["content_hash"],
            "links": list(values["links"]),
            "frontmatter": dict(values["frontmatter"]),
            "provenance": dict(values["provenance"]),
            "source_cursor": values["source_cursor"],
        }
    )


def _snapshot_hash(
    *,
    source_cursor: int,
    status: KnowledgeSnapshotStatus,
    degraded_reason: str | None,
    items: tuple[KnowledgeSnapshotItem, ...],
) -> str:
    return _hash(
        {
            "version": 1,
            "source_cursor": source_cursor,
            "status": status.value,
            "degraded_reason": degraded_reason,
            "items": [item.canonical_payload() for item in items],
        }
    )


def _manifest_hash(manifest: HarnessContextManifest) -> str:
    return _hash(_manifest_payload(manifest))


def _manifest_payload(value: HarnessContextManifest | Mapping[str, Any]) -> dict[str, Any]:
    def field(name: str) -> Any:
        return getattr(value, name) if isinstance(value, HarnessContextManifest) else value[name]

    workspace_version_id = field("workspace_version_id")
    return {
        "version": 1,
        "task_id": str(field("task_id")),
        "scope_digest": field("scope_digest"),
        "workspace_id": str(field("workspace_id")),
        "workspace_version_id": (
            str(workspace_version_id) if workspace_version_id is not None else None
        ),
        "memory_snapshot_id": str(field("memory_snapshot_id")),
        "memory_snapshot_hash": field("memory_snapshot_hash"),
        "knowledge_snapshot_id": str(field("knowledge_snapshot_id")),
        "knowledge_snapshot_hash": field("knowledge_snapshot_hash"),
        "tool_registry_generation": field("tool_registry_generation"),
        "tool_registry_digest": field("tool_registry_digest"),
        "skill_package_digests": list(field("skill_package_digests")),
        "mcp_capability_snapshot": list(field("mcp_capability_snapshot")),
        "model_selection": dict(field("model_selection")),
        "budget": dict(field("budget")),
    }


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(
        json.dumps(
            dict(value),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _required(value: str, name: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} is required and cannot exceed {maximum} characters")
    return normalized


def _digest(value: str, name: str) -> None:
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "HarnessContextManifest",
    "KnowledgeCollection",
    "KnowledgeIngestItem",
    "KnowledgeItem",
    "KnowledgeRevision",
    "KnowledgeSnapshot",
    "KnowledgeSnapshotItem",
    "KnowledgeSnapshotStatus",
    "KnowledgeSource",
    "KnowledgeSourceKind",
    "KnowledgeSourceStatus",
    "KnowledgeSyncRun",
    "KnowledgeSyncStatus",
]
