from __future__ import annotations

from datetime import datetime
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


class FilePresentInput(WorkspaceFileReadInput):
    requested_mode: str = Field(default="auto", pattern=r"^(auto|native|normalized)$")


class FileCompareInput(ContractModel):
    workspace_id: UUID
    left_version_id: UUID
    right_version_id: UUID
    path: str | None = Field(default=None, min_length=1, max_length=4096)


class FileCompareEntryModel(ContractModel):
    path: str
    status: str
    left_hash: str | None
    right_hash: str | None
    left_byte_length: int | None
    right_byte_length: int | None
    text_diff: str | None
    diff_truncated: bool


class FileCompareResultModel(ContractModel):
    workspace_id: UUID
    left_version_id: UUID
    right_version_id: UUID
    items: tuple[FileCompareEntryModel, ...]
    truncated: bool


class FileRenderJobCancelInput(ContractModel):
    job_id: UUID


class DerivedAssetModel(ContractModel):
    id: UUID
    role: str
    media_type: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(ge=0)
    storage_key: str
    metadata: dict[str, object]


class FilePresentationModel(ContractModel):
    id: UUID
    workspace_id: UUID
    version_id: UUID
    file_set_id: UUID
    source_path: str
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    renderer: str
    fidelity: str
    status: str
    capabilities: tuple[str, ...]
    assets: tuple[DerivedAssetModel, ...]
    created_at: datetime


class FileRenderJobModel(ContractModel):
    id: UUID
    workspace_id: UUID
    version_id: UUID
    file_set_id: UUID
    source_path: str
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cache_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_mode: str
    renderer_pack_id: str | None
    renderer_pack_version: str | None
    status: str
    progress: int = Field(ge=0, le=100)
    error_code: str | None
    public_summary: str | None
    created_at: datetime
    updated_at: datetime


class FilePresentationResultModel(ContractModel):
    job: FileRenderJobModel
    presentation: FilePresentationModel | None


class AssetVariantInput(ContractModel):
    path: str = Field(min_length=1, max_length=4096)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    role: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    label: str = Field(min_length=1, max_length=200)


class AssetSetCreateInput(WorkspaceVersionInput):
    version_id: UUID
    kind: str = Field(pattern=r"^(image|audio|video)$")
    title: str = Field(min_length=1, max_length=200)
    variants: tuple[AssetVariantInput, ...] = Field(min_length=1, max_length=64)
    provenance: dict[str, object] = Field(default_factory=dict)
    generation_parameters: dict[str, object] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=512)


class AssetVariantModel(ContractModel):
    path: str
    content_hash: str
    byte_length: int = Field(ge=0)
    media_type: str
    role: str
    label: str


class AssetSetModel(ContractModel):
    id: UUID
    workspace_id: UUID
    version_id: UUID
    kind: str
    title: str
    variants: tuple[AssetVariantModel, ...]
    provenance: dict[str, object]
    generation_parameters: dict[str, object]
    created_at: datetime


class AssetSetPageModel(ContractModel):
    items: tuple[AssetSetModel, ...]


class RendererPackModel(ContractModel):
    id: str
    version: str
    platform: str
    input_media_types: tuple[str, ...]
    output_media_types: tuple[str, ...]
    features: tuple[str, ...]
    limits: dict[str, int]
    license: str
    sandbox: str
    reproducible: bool
    health: str
    installed_at: datetime


class RendererPackPageModel(ContractModel):
    items: tuple[RendererPackModel, ...]


class RendererPackInstallInput(ContractModel):
    bundle_path: str = Field(min_length=1, max_length=4096)
    user_confirmed: bool


class RendererPackRemoveInput(ContractModel):
    pack_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    user_confirmed: bool


class RendererPackRemoveResultModel(ContractModel):
    removed: bool


__all__ = [
    "AssetSetCreateInput",
    "AssetSetModel",
    "AssetSetPageModel",
    "FileCompareInput",
    "FileCompareResultModel",
    "FileDescriptorModel",
    "FilePresentInput",
    "FilePresentationModel",
    "FilePresentationResultModel",
    "FileRenderJobCancelInput",
    "FileRenderJobModel",
    "FileSetGetInput",
    "FileSetMemberModel",
    "FileSetModel",
    "FileSetResolveInput",
    "RendererPackInstallInput",
    "RendererPackModel",
    "RendererPackPageModel",
    "RendererPackRemoveInput",
    "RendererPackRemoveResultModel",
]
