from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from fairy_core.contracts.common import ContractModel
from fairy_core.model_catalog.models import (
    ModelAvailability,
    ModelCategory,
    ModelEndpointKind,
    ModelSelectionMode,
    ProviderCredentialStatus,
)


class ModelCatalogListInput(ContractModel):
    pass


class ModelCatalogRefreshInput(ContractModel):
    pass


class ProviderAccountModel(ContractModel):
    account_id: str = Field(min_length=1, max_length=128)
    provider_kind: str = Field(min_length=1, max_length=32)
    display_name: str = Field(min_length=1, max_length=255)
    credential_status: ProviderCredentialStatus


class ModelPriceModel(ContractModel):
    billable: str = Field(min_length=1, max_length=128)
    unit: str = Field(min_length=1, max_length=128)
    cost_usd: str = Field(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
    variant: str | None = Field(default=None, min_length=1, max_length=128)


class ModelCatalogEntryModel(ContractModel):
    model_id: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    category: ModelCategory
    endpoint_kind: ModelEndpointKind
    description: str = Field(min_length=1, max_length=2_000)
    paid: bool
    availability: ModelAvailability
    unavailable_reason: str | None = Field(default=None, min_length=1, max_length=128)
    input_modalities: tuple[str, ...]
    output_modalities: tuple[str, ...]
    context_length: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    supports_tools: bool
    supports_structured_output: bool
    supports_streaming: bool
    supported_resolutions: tuple[str, ...]
    supported_aspect_ratios: tuple[str, ...]
    prices: tuple[ModelPriceModel, ...]


class ModelCatalogPageModel(ContractModel):
    account: ProviderAccountModel
    items: tuple[ModelCatalogEntryModel, ...]
    fetched_at: datetime
    expires_at: datetime
    stale: bool
    revision: int = Field(ge=0)
    last_error_code: str | None = Field(default=None, min_length=1, max_length=128)


class ModelSelectionGetInput(ContractModel):
    pass


class ModelSelectionUpdateInput(ContractModel):
    mode: ModelSelectionMode
    model_id: str | None = Field(default=None, min_length=1, max_length=255)
    allow_free_fallback: bool = False
    zero_data_retention: bool = False
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_selection_shape(self) -> ModelSelectionUpdateInput:
        if self.mode is ModelSelectionMode.AUTO and self.model_id is not None:
            raise ValueError("auto model selection cannot include a model id")
        if self.mode is ModelSelectionMode.MANUAL and self.model_id is None:
            raise ValueError("manual model selection requires a model id")
        return self


class ModelSelectionPreferenceModel(ContractModel):
    mode: ModelSelectionMode
    model_id: str | None = Field(default=None, min_length=1, max_length=255)
    allow_free_fallback: bool
    zero_data_retention: bool
    revision: int = Field(ge=0)
    updated_at: datetime


__all__ = [
    "ModelCatalogEntryModel",
    "ModelCatalogListInput",
    "ModelCatalogPageModel",
    "ModelCatalogRefreshInput",
    "ModelPriceModel",
    "ModelSelectionGetInput",
    "ModelSelectionPreferenceModel",
    "ModelSelectionUpdateInput",
    "ProviderAccountModel",
]
