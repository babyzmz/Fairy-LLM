from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from fairy_core.contracts.common import ContractModel
from fairy_core.contracts.models import CollectionPageInput, ConversationModel, ProjectModel


class TrashItemType(StrEnum):
    PROJECT = "project"
    CONVERSATION = "conversation"
    PROJECT_CONVERSATION = "project_conversation"


class ProjectMetadataUpdateInput(ContractModel):
    project_id: UUID
    name: str | None = Field(default=None, min_length=1, max_length=255)
    pinned: bool | None = None
    expected_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def require_change(self) -> ProjectMetadataUpdateInput:
        if self.name is None and self.pinned is None:
            raise ValueError("Project metadata update requires name or pinned")
        return self


class ProjectArchiveInput(ContractModel):
    project_id: UUID
    expected_revision: int = Field(ge=0)


class ProjectDeleteInput(ProjectArchiveInput):
    user_confirmed: bool
    cancel_active: bool = False


class ProjectArchivedListInput(CollectionPageInput):
    pass


class ProjectArchivedItemModel(ContractModel):
    project: ProjectModel
    thread_count: int = Field(ge=0)
    archived_at: datetime


class ProjectArchivedPageModel(ContractModel):
    items: tuple[ProjectArchivedItemModel, ...]
    next_cursor: str | None


class TrashListInput(CollectionPageInput):
    pass


class TrashItemModel(ContractModel):
    item_type: TrashItemType
    item_id: UUID
    title: str
    source_project_id: UUID | None
    source_project_title: str | None
    thread_count: int = Field(ge=0)
    deleted_at: datetime
    estimated_bytes: int = Field(ge=0)
    can_restore: bool
    metadata_revision: int = Field(ge=0)


class TrashItemPageModel(ContractModel):
    items: tuple[TrashItemModel, ...]
    next_cursor: str | None


class TrashItemActionInput(ContractModel):
    item_type: TrashItemType
    item_id: UUID
    expected_revision: int = Field(ge=0)
    user_confirmed: bool = False


class TrashPurgeAllInput(ContractModel):
    user_confirmed: bool
    deleted_before: datetime | None = None
    maintenance: bool = False


class TrashPurgeResultModel(ContractModel):
    purged_count: int = Field(ge=0)
    released_bytes: int = Field(ge=0)


class TrashMutationResultModel(ContractModel):
    item_type: TrashItemType
    item_id: UUID
    status: Literal["restored", "purged"]
    released_bytes: int = Field(ge=0)


class ConversationUpdateInput(ContractModel):
    conversation_id: UUID
    title: str | None = Field(default=None, min_length=1, max_length=200)
    pinned: bool | None = None
    expected_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def require_change(self) -> ConversationUpdateInput:
        if self.title is None and self.pinned is None:
            raise ValueError("Conversation update requires title or pinned")
        return self


class ConversationDeleteInput(ContractModel):
    conversation_id: UUID
    expected_revision: int = Field(ge=0)
    user_confirmed: bool


class ConversationMoveToProjectInput(ContractModel):
    conversation_id: UUID
    target_project_id: UUID
    expected_revision: int = Field(ge=0)
    user_confirmed: bool
    idempotency_key: str = Field(min_length=1, max_length=512)


class TaskMetadataUpdateInput(ContractModel):
    task_id: UUID
    display_title: str | None = Field(default=None, min_length=1, max_length=200)
    pinned: bool | None = None
    expected_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def require_change(self) -> TaskMetadataUpdateInput:
        if self.display_title is None and self.pinned is None:
            raise ValueError("Task metadata update requires display_title or pinned")
        return self


class TaskArchiveInput(ContractModel):
    task_id: UUID
    expected_revision: int = Field(ge=0)


class ConversationMoveResultModel(ContractModel):
    source_conversation: ConversationModel
    destination_conversation: ConversationModel
    imported_count: int = Field(ge=0)


__all__ = [
    "ConversationDeleteInput",
    "ConversationMoveResultModel",
    "ConversationMoveToProjectInput",
    "ConversationUpdateInput",
    "ProjectArchiveInput",
    "ProjectArchivedItemModel",
    "ProjectArchivedListInput",
    "ProjectArchivedPageModel",
    "ProjectDeleteInput",
    "ProjectMetadataUpdateInput",
    "TaskArchiveInput",
    "TaskMetadataUpdateInput",
    "TrashItemActionInput",
    "TrashItemModel",
    "TrashItemPageModel",
    "TrashItemType",
    "TrashListInput",
    "TrashMutationResultModel",
    "TrashPurgeAllInput",
    "TrashPurgeResultModel",
]
