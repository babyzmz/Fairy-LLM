from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from fairy_core.contracts.common import ContractModel
from fairy_core.domain.models import ProjectResidency, WorkspaceType


class ProjectModel(ContractModel):
    id: UUID
    name: str
    residency: ProjectResidency
    workspace_id: UUID
    active_version_id: UUID | None
    active_preview_id: UUID | None
    revision: int = Field(ge=0)
    pinned_at: datetime | None
    archived_at: datetime | None
    deleted_at: datetime | None
    purged_at: datetime | None
    metadata_revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class ConversationModel(ContractModel):
    id: UUID
    project_id: UUID | None
    workspace_id: UUID
    workspace_type: WorkspaceType
    base_version_id: UUID | None
    active_draft_version_id: UUID | None
    active_task_id: UUID | None
    active_preview_id: UUID | None
    title: str
    pinned_at: datetime | None
    deleted_at: datetime | None
    deleted_by_project_at: datetime | None
    purged_at: datetime | None
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


__all__ = ["ConversationModel", "ProjectModel"]
