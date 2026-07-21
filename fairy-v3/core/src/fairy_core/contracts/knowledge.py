from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from fairy_core.contracts.common import ContractModel


class KnowledgeProjectInput(ContractModel):
    project_id: UUID


class KnowledgeTaskInput(ContractModel):
    task_id: UUID


class KnowledgeSnapshotGetInput(KnowledgeTaskInput):
    snapshot_id: UUID


class HarnessManifestGetInput(KnowledgeTaskInput):
    manifest_id: UUID


class KnowledgeRevisionReadInput(KnowledgeTaskInput):
    snapshot_id: UUID
    revision_id: UUID


class KnowledgeSearchInput(KnowledgeTaskInput):
    snapshot_id: UUID
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=20, ge=1, le=50)


class KnowledgeItemListInput(KnowledgeProjectInput):
    query: str | None = Field(default=None, max_length=500)
    limit: int = Field(default=500, ge=1, le=1000)


class KnowledgeSyncStartInput(ContractModel):
    source_id: UUID
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=255)


class KnowledgeSyncRunInput(ContractModel):
    run_id: UUID


class KnowledgeTransportAvailability(StrEnum):
    LOCAL_DEVICE_ONLY = "local_device_only"
    NORMALIZED_CONTENT_ONLY = "normalized_content_only"


class KnowledgeNodeKind(StrEnum):
    PROJECT = "project"
    CONVERSATION = "conversation"
    FOLDER = "folder"
    FILE = "file"
    NOTE = "note"
    ARTIFACT = "artifact"
    MEMORY = "memory"
    TASK = "task"
    OBSIDIAN = "obsidian"


class KnowledgeRelationKind(StrEnum):
    CONTAINS = "contains"
    IMPORTS = "imports"
    REFERENCES = "references"
    DERIVED_FROM = "derived_from"
    DISCUSSED_IN = "discussed_in"


class KnowledgeItemModel(ContractModel):
    id: str
    project_id: UUID
    kind: KnowledgeNodeKind
    title: str
    relative_path: str | None = None
    content_hash: str | None = None
    byte_length: int | None = None
    language: str | None = None
    revision: int


class KnowledgeItemPageModel(ContractModel):
    items: tuple[KnowledgeItemModel, ...]
    source_revision: int
    watermark: str


class KnowledgeGraphNodeModel(KnowledgeItemModel):
    conversation_id: UUID | None = None
    task_id: UUID | None = None
    source_id: UUID | None = None
    revision_id: UUID | None = None


class KnowledgeGraphEdgeModel(ContractModel):
    id: str
    source_id: str
    target_id: str
    relation: KnowledgeRelationKind


class KnowledgeGraphModel(ContractModel):
    project_id: UUID
    source_version_id: UUID | None
    source_revision: int
    watermark: str
    nodes: tuple[KnowledgeGraphNodeModel, ...]
    edges: tuple[KnowledgeGraphEdgeModel, ...]


class ProjectKnowledgeOverviewModel(ContractModel):
    project_id: UUID
    source_version_id: UUID | None
    source_revision: int
    watermark: str
    file_count: int
    note_count: int
    conversation_count: int
    relation_count: int
    obsidian_connected: bool
    obsidian_health: str


class KnowledgeSourceModel(ContractModel):
    id: UUID
    project_id: UUID
    kind: str
    display_name: str
    display_path: str
    status: str
    revision: int
    sync_cursor: int
    transport_availability: KnowledgeTransportAvailability = (
        KnowledgeTransportAvailability.LOCAL_DEVICE_ONLY
    )
    created_at: datetime
    updated_at: datetime


class KnowledgeSourcePageModel(ContractModel):
    items: tuple[KnowledgeSourceModel, ...]


class KnowledgeCollectionModel(ContractModel):
    id: UUID
    source_id: UUID
    project_id: UUID
    read_scope: str
    allowed_directories: tuple[str, ...]
    managed_directory: str
    scope_kind: str
    revision: int
    created_at: datetime
    updated_at: datetime


class KnowledgeCollectionPageModel(ContractModel):
    items: tuple[KnowledgeCollectionModel, ...]


class KnowledgeSyncRunModel(ContractModel):
    id: UUID
    source_id: UUID
    project_id: UUID
    status: str
    expected_source_revision: int
    source_cursor: int
    scanned_count: int
    changed_count: int
    deleted_count: int
    failed_count: int
    error_code: str | None
    attempts: int
    cancellation_revision: int
    started_at: datetime
    completed_at: datetime | None


class KnowledgeRevisionModel(ContractModel):
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
    frontmatter: dict[str, Any]
    provenance: dict[str, Any]
    source_cursor: int
    created_at: datetime


class KnowledgeRevisionPageModel(ContractModel):
    items: tuple[KnowledgeRevisionModel, ...]
    snapshot_id: UUID
    snapshot_hash: str


class KnowledgeLinkPageModel(ContractModel):
    revision_id: UUID
    links: tuple[str, ...]
    snapshot_id: UUID


class KnowledgeSnapshotItemModel(ContractModel):
    ordinal: int
    item_id: UUID
    revision_id: UUID
    source_id: UUID
    relative_path: str
    title: str
    content_hash: str
    revision_hash: str


class KnowledgeSnapshotModel(ContractModel):
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    source_cursor: int
    status: str
    degraded_reason: str | None
    content_hash: str
    items: tuple[KnowledgeSnapshotItemModel, ...]
    created_at: datetime


class HarnessContextManifestModel(ContractModel):
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
    skill_package_digests: tuple[str, ...]
    mcp_capability_snapshot: tuple[str, ...]
    model_selection: dict[str, Any]
    budget: dict[str, Any]
    content_hash: str
    created_at: datetime


__all__ = [
    "HarnessContextManifestModel",
    "HarnessManifestGetInput",
    "KnowledgeCollectionModel",
    "KnowledgeCollectionPageModel",
    "KnowledgeGraphEdgeModel",
    "KnowledgeGraphModel",
    "KnowledgeGraphNodeModel",
    "KnowledgeItemListInput",
    "KnowledgeItemModel",
    "KnowledgeItemPageModel",
    "KnowledgeLinkPageModel",
    "KnowledgeNodeKind",
    "KnowledgeProjectInput",
    "KnowledgeRelationKind",
    "KnowledgeRevisionModel",
    "KnowledgeRevisionPageModel",
    "KnowledgeRevisionReadInput",
    "KnowledgeSearchInput",
    "KnowledgeSnapshotGetInput",
    "KnowledgeSnapshotItemModel",
    "KnowledgeSnapshotModel",
    "KnowledgeSourceModel",
    "KnowledgeSourcePageModel",
    "KnowledgeSyncRunInput",
    "KnowledgeSyncRunModel",
    "KnowledgeSyncStartInput",
    "KnowledgeTaskInput",
    "KnowledgeTransportAvailability",
    "ProjectKnowledgeOverviewModel",
]
