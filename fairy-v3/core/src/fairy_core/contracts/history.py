from __future__ import annotations

from uuid import UUID

from pydantic import Field, model_validator

from fairy_core.contracts.common import ContractModel
from fairy_core.contracts.models import ConversationModel


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
    "TaskArchiveInput",
    "TaskMetadataUpdateInput",
]
