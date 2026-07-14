from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from fairy_core.contracts.common import ContractModel
from fairy_core.contracts.models import (
    ApprovalModel,
    ChangesetModel,
    ConversationModel,
    FileMutation,
    FileMutationOperation,
    TaskModel,
    VersionModel,
)


class WorkspaceIdInput(ContractModel):
    workspace_id: UUID


class WorkspaceVersionInput(WorkspaceIdInput):
    version_id: UUID | None = None


class WorkspaceFileReadInput(WorkspaceVersionInput):
    path: str = Field(min_length=1, max_length=1_024)

    @field_validator("path")
    @classmethod
    def require_relative_path(cls, value: str) -> str:
        normalized = value.strip().replace("\\", "/")
        if (
            not normalized
            or normalized.startswith("/")
            or ":" in normalized
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
        ):
            raise ValueError("path must be a normalized Workspace-relative path")
        return normalized


class WorkspaceFileStreamInput(WorkspaceFileReadInput):
    expires_seconds: int = Field(default=120, ge=1, le=300)


class WorkspaceModel(ContractModel):
    id: UUID
    active_version_id: UUID | None
    active_preview_id: UUID | None
    revision: int = Field(ge=0)
    max_files: int = Field(gt=0)
    max_bytes: int = Field(gt=0)
    created_at: datetime
    updated_at: datetime


class WorkspaceVersionModel(VersionModel):
    pass


class WorkspaceFileModel(ContractModel):
    path: str
    byte_length: int = Field(ge=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: str
    language: str | None


class WorkspaceFilePageModel(ContractModel):
    workspace_id: UUID
    version_id: UUID
    generation: int = Field(ge=1)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: tuple[WorkspaceFileModel, ...]


class WorkspaceFileContentModel(ContractModel):
    file: WorkspaceFileModel
    media_type: str
    text: str | None = None
    stream_required: bool = False

    @model_validator(mode="after")
    def require_content_or_stream(self) -> WorkspaceFileContentModel:
        if (self.text is None) != self.stream_required:
            raise ValueError("exactly one inline text or stream_required result is required")
        return self


class FileReadSessionModel(ContractModel):
    session_id: UUID
    workspace_id: UUID
    version_id: UUID
    path: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(ge=0)
    media_type: str
    url: str
    expires_at: datetime


class WorkspaceFileMutateInput(WorkspaceIdInput):
    conversation_id: UUID
    expected_workspace_revision: int = Field(ge=0)
    files: tuple[FileMutation, ...] = Field(min_length=1, max_length=25)
    reason: str = Field(min_length=1, max_length=10_000)
    idempotency_key: str = Field(min_length=1, max_length=255)
    user_confirmed: bool

    @model_validator(mode="after")
    def require_strict_file_operations(self) -> WorkspaceFileMutateInput:
        if any(file.operation is FileMutationOperation.UPSERT for file in self.files):
            raise ValueError("Workspace file mutations require an explicit operation")
        return self


class WorkspaceFileMutationResultModel(ContractModel):
    workspace: WorkspaceModel
    conversation: ConversationModel
    task: TaskModel
    target_version: VersionModel
    changeset: ChangesetModel
    approval: ApprovalModel


class WorkspaceExportInput(WorkspaceVersionInput):
    filename: str = Field(default="fairy-workspace.zip", min_length=1, max_length=255)

    @field_validator("filename")
    @classmethod
    def require_zip_filename(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.lower().endswith(".zip") or any(
            character in normalized for character in '<>:"/\\|?*'
        ):
            raise ValueError("filename must be a safe .zip filename")
        return normalized


class WorkspaceExportModel(ContractModel):
    filename: str
    media_type: str
    byte_length: int = Field(ge=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_base64: str


__all__ = [
    "FileReadSessionModel",
    "WorkspaceExportInput",
    "WorkspaceExportModel",
    "WorkspaceFileContentModel",
    "WorkspaceFileModel",
    "WorkspaceFileMutateInput",
    "WorkspaceFileMutationResultModel",
    "WorkspaceFilePageModel",
    "WorkspaceFileReadInput",
    "WorkspaceFileStreamInput",
    "WorkspaceIdInput",
    "WorkspaceModel",
    "WorkspaceVersionInput",
    "WorkspaceVersionModel",
]
