from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from fairy_core.contracts.common import ContractModel


class ObsidianConnectorHealthModel(ContractModel):
    desktop_installed: bool
    cli_available: bool
    minimum_installer_version: str = "1.12.7"
    status: str
    public_summary: str


class ObsidianSourceMode(StrEnum):
    READ_ONLY = "read_only"
    BIDIRECTIONAL = "bidirectional"


class ObsidianSourceCreateInput(ContractModel):
    project_id: UUID
    display_name: str = Field(min_length=1, max_length=200)
    vault_path: str = Field(min_length=1, max_length=1024)
    allowed_directories: tuple[str, ...] = ()
    managed_directory: str = "Fairy"
    mode: ObsidianSourceMode = ObsidianSourceMode.READ_ONLY
    idempotency_key: str = Field(min_length=1, max_length=255)


class ObsidianSourceListInput(ContractModel):
    project_id: UUID


class ObsidianSourceIdInput(ContractModel):
    source_id: UUID


class ObsidianSourceSyncInput(ContractModel):
    source_id: UUID
    expected_revision: int = Field(ge=1)


class ObsidianSourceModel(ContractModel):
    id: UUID
    project_id: UUID
    display_name: str
    vault_display_path: str
    allowed_directories: tuple[str, ...]
    managed_directory: str
    mode: ObsidianSourceMode
    status: str
    revision: int
    item_count: int
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ObsidianSourcePageModel(ContractModel):
    items: tuple[ObsidianSourceModel, ...]


class ObsidianVaultItemModel(ContractModel):
    source_id: UUID
    relative_path: str
    title: str
    kind: str
    content_hash: str
    byte_length: int
    links: tuple[str, ...]
    modified_at: datetime


class ObsidianVaultItemPageModel(ContractModel):
    items: tuple[ObsidianVaultItemModel, ...]
    source_revision: int


class ObsidianSyncResultModel(ContractModel):
    source: ObsidianSourceModel
    scanned_count: int
    changed_count: int
    deleted_count: int
    failed_count: int


__all__ = [
    "ObsidianConnectorHealthModel",
    "ObsidianSourceCreateInput",
    "ObsidianSourceIdInput",
    "ObsidianSourceListInput",
    "ObsidianSourceMode",
    "ObsidianSourceModel",
    "ObsidianSourcePageModel",
    "ObsidianSourceSyncInput",
    "ObsidianSyncResultModel",
    "ObsidianVaultItemModel",
    "ObsidianVaultItemPageModel",
]
