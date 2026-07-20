from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

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


class ObsidianReadScope(StrEnum):
    SELECTED_DIRECTORIES = "selected_directories"
    WHOLE_VAULT = "whole_vault"


class ObsidianSourceCreateInput(ContractModel):
    project_id: UUID
    display_name: str = Field(min_length=1, max_length=200)
    local_path_token: str = Field(min_length=36, max_length=36)
    read_scope: ObsidianReadScope = ObsidianReadScope.SELECTED_DIRECTORIES
    allowed_directories: tuple[str, ...] = ()
    whole_vault_confirmed: bool = False
    managed_directory: str = "Fairy"
    mode: ObsidianSourceMode = ObsidianSourceMode.READ_ONLY
    idempotency_key: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_read_scope(self) -> Self:
        if self.read_scope is ObsidianReadScope.SELECTED_DIRECTORIES:
            if not self.allowed_directories:
                raise ValueError("Select at least one Vault directory")
            if self.whole_vault_confirmed:
                raise ValueError("Whole-Vault confirmation does not apply to selected directories")
        elif self.allowed_directories or not self.whole_vault_confirmed:
            raise ValueError(
                "Whole-Vault access requires explicit confirmation and no directory list"
            )
        return self


class ObsidianSourceListInput(ContractModel):
    project_id: UUID


class ObsidianSourceIdInput(ContractModel):
    source_id: UUID


class ObsidianSourceSyncInput(ContractModel):
    source_id: UUID
    expected_revision: int = Field(ge=1)


class ObsidianVaultItemReadInput(ContractModel):
    source_id: UUID
    relative_path: str = Field(min_length=1, max_length=1024)
    expected_source_revision: int = Field(ge=1)
    expected_content_hash: str = Field(min_length=64, max_length=64)


class ObsidianSourceModel(ContractModel):
    id: UUID
    project_id: UUID
    display_name: str
    vault_display_path: str
    read_scope: ObsidianReadScope
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


class ObsidianVaultItemContentModel(ContractModel):
    source_id: UUID
    relative_path: str
    title: str
    kind: str
    content_hash: str
    content: str


class ObsidianSyncResultModel(ContractModel):
    source: ObsidianSourceModel
    scanned_count: int
    changed_count: int
    deleted_count: int
    failed_count: int


__all__ = [
    "ObsidianConnectorHealthModel",
    "ObsidianReadScope",
    "ObsidianSourceCreateInput",
    "ObsidianSourceIdInput",
    "ObsidianSourceListInput",
    "ObsidianSourceMode",
    "ObsidianSourceModel",
    "ObsidianSourcePageModel",
    "ObsidianSourceSyncInput",
    "ObsidianSyncResultModel",
    "ObsidianVaultItemContentModel",
    "ObsidianVaultItemModel",
    "ObsidianVaultItemPageModel",
    "ObsidianVaultItemReadInput",
]
