from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fairy_core.contracts.model_catalog import (
    ModelCatalogPageModel,
    ModelSelectionPreferenceModel,
    ModelSelectionUpdateInput,
)
from fastapi import APIRouter, Header

SyncInvoke = Callable[[str, dict[str, Any]], dict[str, Any]]
IdempotencyGuard = Callable[[str, str], None]


def install_model_routes(
    router: APIRouter,
    *,
    invoke: SyncInvoke,
    guard: IdempotencyGuard,
) -> None:
    @router.get(
        "/models/catalog",
        operation_id="models.catalog.list",
        response_model=ModelCatalogPageModel,
    )
    def list_model_catalog() -> dict[str, Any]:
        return invoke("models.catalog.list", {})

    @router.post(
        "/models/catalog/refresh",
        operation_id="models.catalog.refresh",
        response_model=ModelCatalogPageModel,
    )
    def refresh_model_catalog() -> dict[str, Any]:
        return invoke("models.catalog.refresh", {})

    @router.get(
        "/models/selection",
        operation_id="models.selection.get",
        response_model=ModelSelectionPreferenceModel,
    )
    def get_model_selection() -> dict[str, Any]:
        return invoke("models.selection.get", {})

    @router.put(
        "/models/selection",
        operation_id="models.selection.update",
        response_model=ModelSelectionPreferenceModel,
    )
    def update_model_selection(
        request: ModelSelectionUpdateInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=512),
        ],
    ) -> dict[str, Any]:
        guard(request.idempotency_key, idempotency_key)
        return invoke("models.selection.update", request.model_dump(mode="json"))


__all__ = ["install_model_routes"]
