from __future__ import annotations

from uuid import UUID

from pydantic import Field

from fairy_core.contracts.common import ContractModel
from fairy_core.contracts.workspaces import WorkspaceFileReadInput, WorkspaceVersionInput


class FileDescriptorModel(ContractModel):
    path: str
    byte_length: int = Field(ge=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: str
    language: str | None
    extension: str | None
    media_type: str
    claimed_media_type: str | None
    media_type_conflict: bool


class FileSetMemberModel(ContractModel):
    path: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(ge=0)
    role: str


class FileSetModel(ContractModel):
    id: UUID
    workspace_id: UUID
    version_id: UUID
    kind: str
    primary_path: str
    parser_version: str
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    members: tuple[FileSetMemberModel, ...]
    missing_dependencies: tuple[str, ...]
    blocked_dependencies: tuple[str, ...]


class FileSetResolveInput(WorkspaceFileReadInput):
    pass


class FileSetGetInput(WorkspaceVersionInput):
    file_set_id: UUID


__all__ = [
    "FileDescriptorModel",
    "FileSetGetInput",
    "FileSetMemberModel",
    "FileSetModel",
    "FileSetResolveInput",
]
