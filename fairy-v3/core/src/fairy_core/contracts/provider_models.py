from __future__ import annotations

from pydantic import Field

from fairy_core.contracts.common import ContractModel
from fairy_core.providers.models import (
    ProviderCapability,
    ProviderHealthStatus,
    ProviderKind,
)


class ProviderHealthInput(ContractModel):
    profile_id: str | None = Field(default=None, min_length=1, max_length=128)


class ProviderProfileModel(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=255)
    kind: ProviderKind
    base_url: str = Field(min_length=1, max_length=2_048)
    model_id: str = Field(min_length=1, max_length=255)
    capabilities: tuple[ProviderCapability, ...]
    fallback_profile_id: str | None = Field(default=None, max_length=128)
    timeout_seconds: float = Field(gt=0, le=300)
    enabled: bool
    credential_required: bool
    credential_configured: bool


class ProviderProfilePageModel(ContractModel):
    items: tuple[ProviderProfileModel, ...]


class ProviderHealthModel(ContractModel):
    profile_id: str = Field(min_length=1, max_length=128)
    status: ProviderHealthStatus
    error_code: str | None = Field(default=None, max_length=128)
    diagnostics: tuple[str, ...]


class ProviderHealthPageModel(ContractModel):
    items: tuple[ProviderHealthModel, ...]
