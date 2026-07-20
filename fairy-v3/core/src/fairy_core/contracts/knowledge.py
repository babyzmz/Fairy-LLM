from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from fairy_core.contracts.common import ContractModel


class KnowledgeProjectInput(ContractModel):
    project_id: UUID


class KnowledgeItemListInput(KnowledgeProjectInput):
    query: str | None = Field(default=None, max_length=500)
    limit: int = Field(default=500, ge=1, le=1000)


class KnowledgeNodeKind(StrEnum):
    PROJECT = "project"
    CONVERSATION = "conversation"
    FOLDER = "folder"
    FILE = "file"
    NOTE = "note"
    ARTIFACT = "artifact"
    MEMORY = "memory"


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


class KnowledgeGraphNodeModel(KnowledgeItemModel):
    conversation_id: UUID | None = None


class KnowledgeGraphEdgeModel(ContractModel):
    id: str
    source_id: str
    target_id: str
    relation: KnowledgeRelationKind


class KnowledgeGraphModel(ContractModel):
    project_id: UUID
    source_version_id: UUID | None
    source_revision: int
    nodes: tuple[KnowledgeGraphNodeModel, ...]
    edges: tuple[KnowledgeGraphEdgeModel, ...]


class ProjectKnowledgeOverviewModel(ContractModel):
    project_id: UUID
    source_version_id: UUID | None
    source_revision: int
    file_count: int
    note_count: int
    conversation_count: int
    relation_count: int
    obsidian_connected: bool
    obsidian_health: str


__all__ = [
    "KnowledgeGraphEdgeModel",
    "KnowledgeGraphModel",
    "KnowledgeGraphNodeModel",
    "KnowledgeItemListInput",
    "KnowledgeItemModel",
    "KnowledgeItemPageModel",
    "KnowledgeNodeKind",
    "KnowledgeProjectInput",
    "KnowledgeRelationKind",
    "ProjectKnowledgeOverviewModel",
]
