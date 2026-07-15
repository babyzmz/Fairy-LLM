from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from fairy_core.model_catalog.models import (
    ModelCatalogEntry,
    ModelCatalogSnapshot,
    ModelSelectionMode,
    ModelSelectionPreference,
    ProviderCredentialStatus,
)


@dataclass(frozen=True, slots=True)
class ModelCatalogFetchResult:
    entries: tuple[ModelCatalogEntry, ...]
    credential_status: ProviderCredentialStatus
    fetched_at: datetime


class ModelCatalogSourceError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        *,
        credential_status: ProviderCredentialStatus | None = None,
    ) -> None:
        self.error_code = error_code
        self.credential_status = credential_status
        super().__init__(error_code)


class ModelCatalogSource(Protocol):
    def fetch(self) -> ModelCatalogFetchResult: ...

    def close(self) -> None: ...


class ModelCatalogRepository(Protocol):
    def get_catalog(self) -> ModelCatalogSnapshot | None: ...

    def replace_catalog(
        self,
        snapshot: ModelCatalogSnapshot,
        *,
        expected_revision: int,
    ) -> ModelCatalogSnapshot: ...

    def get_selection(self) -> ModelSelectionPreference: ...

    def update_selection(
        self,
        *,
        mode: ModelSelectionMode,
        model_id: str | None,
        allow_free_fallback: bool,
        zero_data_retention: bool,
        expected_revision: int,
        idempotency_key: str,
    ) -> ModelSelectionPreference: ...


__all__ = [
    "ModelCatalogFetchResult",
    "ModelCatalogRepository",
    "ModelCatalogSource",
    "ModelCatalogSourceError",
]
