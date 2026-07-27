from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, cast

from pydantic import BaseModel

from fairy_core.contracts.model_catalog import ModelSelectionUpdateInput
from fairy_core.model_catalog.application import ModelCatalogApplication
from fairy_core.model_catalog.models import (
    ModelCatalogSnapshot,
    ModelSelectionPreference,
    ProviderCredentialStatus,
)
from fairy_core.model_catalog.ports import ModelCatalogSource
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import ProviderRegistry

Handler = Callable[[BaseModel], Any]


class ModelCatalogService:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        provider_registry: ProviderRegistry,
        source: ModelCatalogSource | None,
    ) -> None:
        self._provider_registry = provider_registry
        self._application = ModelCatalogApplication(
            unit_of_work_factory=unit_of_work_factory,
            source=source,
            credential_status=self._openrouter_credential_status,
        )
        self.handlers: Mapping[str, Handler] = MappingProxyType(
            {
                "models.catalog.list": self.list_catalog,
                "models.catalog.refresh": self.refresh_catalog,
                "models.selection.get": self.get_selection,
                "models.selection.update": self.update_selection,
            }
        )

    def close(self) -> None:
        self._application.close()

    def list_catalog(self, _request: BaseModel) -> dict[str, Any]:
        return _catalog_response(self._application.list_catalog())

    def refresh_catalog(self, _request: BaseModel) -> dict[str, Any]:
        return _catalog_response(self._application.refresh_catalog())

    def get_selection(self, _request: BaseModel) -> dict[str, Any]:
        return _selection_response(self._application.get_selection())

    def selection_preference(self) -> ModelSelectionPreference:
        """Return the typed current selection for other Core-owned workflows."""
        return self._application.get_selection()

    def update_selection(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(ModelSelectionUpdateInput, request)
        selection = self._application.update_selection(
            mode=validated.mode,
            model_id=validated.model_id,
            allow_free_fallback=validated.allow_free_fallback,
            zero_data_retention=validated.zero_data_retention,
            expected_revision=validated.expected_revision,
            idempotency_key=validated.idempotency_key,
        )
        return _selection_response(selection)

    def _openrouter_credential_status(self) -> ProviderCredentialStatus:
        profiles = tuple(
            profile
            for profile in self._provider_registry.list_public()
            if "openrouter.ai" in profile.base_url.casefold()
        )
        if not profiles:
            return ProviderCredentialStatus.UNAVAILABLE
        return (
            ProviderCredentialStatus.CONFIGURED
            if any(profile.credential_configured for profile in profiles)
            else ProviderCredentialStatus.UNAVAILABLE
        )


def _catalog_response(snapshot: ModelCatalogSnapshot) -> dict[str, Any]:
    return {
        "account": {
            "account_id": snapshot.account.account_id,
            "provider_kind": snapshot.account.provider_kind,
            "display_name": snapshot.account.display_name,
            "credential_status": snapshot.account.credential_status,
        },
        "items": [
            {
                "model_id": entry.model_id,
                "display_name": entry.display_name,
                "category": entry.category,
                "endpoint_kind": entry.endpoint_kind,
                "description": entry.description,
                "paid": entry.paid,
                "availability": entry.availability,
                "unavailable_reason": entry.unavailable_reason,
                "input_modalities": entry.input_modalities,
                "output_modalities": entry.output_modalities,
                "context_length": entry.context_length,
                "max_output_tokens": entry.max_output_tokens,
                "supports_tools": entry.supports_tools,
                "supports_structured_output": entry.supports_structured_output,
                "supports_streaming": entry.supports_streaming,
                "supported_resolutions": entry.supported_resolutions,
                "supported_aspect_ratios": entry.supported_aspect_ratios,
                "prices": [
                    {
                        "billable": price.billable,
                        "unit": price.unit,
                        "cost_usd": price.cost_usd,
                        "variant": price.variant,
                    }
                    for price in entry.prices
                ],
            }
            for entry in snapshot.entries
        ],
        "fetched_at": snapshot.fetched_at,
        "expires_at": snapshot.expires_at,
        "stale": snapshot.is_stale(datetime.now(UTC)),
        "revision": snapshot.revision,
        "last_error_code": snapshot.last_error_code,
    }


def _selection_response(selection: ModelSelectionPreference) -> dict[str, Any]:
    return {
        "mode": selection.mode,
        "model_id": selection.model_id,
        "allow_free_fallback": selection.allow_free_fallback,
        "zero_data_retention": selection.zero_data_retention,
        "revision": selection.revision,
        "updated_at": selection.updated_at,
    }


__all__ = ["ModelCatalogService"]
