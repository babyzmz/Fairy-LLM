from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.model_catalog.models import (
    MODEL_ALLOWLIST,
    ModelAvailability,
    ModelCatalogEntry,
    ProviderCredentialStatus,
)
from fairy_core.model_catalog.ports import (
    ModelCatalogFetchResult,
    ModelCatalogSourceError,
)
from fairy_core.transports.stdio import build_local_service


class FakeCatalogSource:
    def __init__(self) -> None:
        self.error: ModelCatalogSourceError | None = None
        self.closed = False

    def fetch(self) -> ModelCatalogFetchResult:
        if self.error is not None:
            raise self.error
        return ModelCatalogFetchResult(
            entries=tuple(
                ModelCatalogEntry(
                    model_id=allowed.model_id,
                    display_name=allowed.display_name,
                    category=allowed.category,
                    endpoint_kind=allowed.endpoint_kind,
                    description=allowed.description,
                    paid=allowed.paid,
                    availability=ModelAvailability.AVAILABLE,
                    input_modalities=("text",),
                    output_modalities=(
                        (
                            "text"
                            if allowed.endpoint_kind.value == "chat"
                            else allowed.endpoint_kind.value
                        ),
                    ),
                    supports_tools=allowed.endpoint_kind.value == "chat",
                    supports_structured_output=allowed.endpoint_kind.value == "chat",
                )
                for allowed in MODEL_ALLOWLIST
            ),
            credential_status=ProviderCredentialStatus.CONFIGURED,
            fetched_at=datetime.now(UTC),
        )

    def close(self) -> None:
        self.closed = True


def test_catalog_refresh_persists_complete_allowlist_and_last_known_good(tmp_path) -> None:
    source = FakeCatalogSource()
    service = build_local_service(tmp_path, model_catalog_source=source)
    try:
        initial = service.invoke("models.catalog.list", {})
        assert initial["revision"] == 0
        assert initial["stale"] is False
        assert len(initial["items"]) == 8

        refreshed = service.invoke("models.catalog.refresh", {})
        assert refreshed["revision"] == 1
        assert refreshed["account"]["credential_status"] == "configured"
        assert {item["availability"] for item in refreshed["items"]} == {"available"}

        source.error = ModelCatalogSourceError(
            "CREDENTIAL_INVALID",
            credential_status=ProviderCredentialStatus.INVALID,
        )
        failed = service.invoke("models.catalog.refresh", {})
        assert failed["revision"] == 2
        assert failed["account"]["credential_status"] == "invalid"
        assert failed["last_error_code"] == "CREDENTIAL_INVALID"
        assert {item["availability"] for item in failed["items"]} == {"available"}
    finally:
        service.close()
    assert source.closed is True


def test_global_selection_is_revision_fenced_idempotent_and_persistent(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        default = service.invoke("models.selection.get", {})
        assert default == {
            "mode": "auto",
            "model_id": None,
            "allow_free_fallback": False,
            "zero_data_retention": False,
            "revision": 0,
            "updated_at": default["updated_at"],
        }
        update = {
            "mode": "manual",
            "model_id": "moonshotai/kimi-k2.7-code",
            "allow_free_fallback": False,
            "zero_data_retention": True,
            "expected_revision": 0,
            "idempotency_key": "selection-1",
        }
        saved = service.invoke("models.selection.update", update)
        replayed = service.invoke("models.selection.update", update)
        assert replayed == saved
        assert saved["revision"] == 1
        assert saved["model_id"] == "moonshotai/kimi-k2.7-code"

        with pytest.raises(IdempotencyConflictError):
            service.invoke(
                "models.selection.update",
                {**update, "model_id": "z-ai/glm-5.2"},
            )
        with pytest.raises(VersionConflictError):
            service.invoke(
                "models.selection.update",
                {
                    **update,
                    "idempotency_key": "selection-stale",
                    "model_id": "z-ai/glm-5.2",
                },
            )
    finally:
        service.close()

    reopened = build_local_service(tmp_path)
    try:
        assert reopened.invoke("models.selection.get", {})["model_id"] == (
            "moonshotai/kimi-k2.7-code"
        )
    finally:
        reopened.close()


def test_manual_selection_rejects_models_outside_the_allowlist(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        with pytest.raises(ValueError, match="allowlisted"):
            service.invoke(
                "models.selection.update",
                {
                    "mode": "manual",
                    "model_id": "openrouter/free",
                    "allow_free_fallback": False,
                    "zero_data_retention": False,
                    "expected_revision": 0,
                    "idempotency_key": "selection-invalid",
                },
            )
    finally:
        service.close()
