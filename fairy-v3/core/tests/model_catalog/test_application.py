from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event

import pytest

from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.model_catalog.application import ModelCatalogApplication
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
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory, create_sqlite_core_engine
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


class TrackingUnitOfWorkFactory:
    def __init__(self, delegate: SqlAlchemyUnitOfWorkFactory) -> None:
        self.delegate = delegate
        self.active = 0

    def __call__(self):
        delegate = self.delegate()
        owner = self

        class TrackedUnitOfWork:
            def __enter__(self):
                owner.active += 1
                try:
                    return delegate.__enter__()
                except BaseException:
                    owner.active -= 1
                    raise

            def __exit__(self, exc_type, exc_value, traceback):
                try:
                    return delegate.__exit__(exc_type, exc_value, traceback)
                finally:
                    owner.active -= 1

        return TrackedUnitOfWork()


class UnitOfWorkObservingCatalogSource(FakeCatalogSource):
    def __init__(self, factory: TrackingUnitOfWorkFactory) -> None:
        super().__init__()
        self.factory = factory
        self.active_during_fetch: int | None = None

    def fetch(self) -> ModelCatalogFetchResult:
        self.active_during_fetch = self.factory.active
        return super().fetch()


class WaitingCatalogSource(FakeCatalogSource):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def fetch(self) -> ModelCatalogFetchResult:
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise AssertionError("catalog refresh was not released")
        return super().fetch()


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

    reopened = build_local_service(tmp_path)
    try:
        persisted = reopened.invoke("models.catalog.list", {})
        assert persisted["revision"] == 2
        assert persisted["last_error_code"] == "CREDENTIAL_INVALID"
        assert {item["availability"] for item in persisted["items"]} == {"available"}
    finally:
        reopened.close()


def test_catalog_refresh_does_not_hold_database_unit_of_work_during_network_fetch(
    tmp_path,
) -> None:
    engine = create_sqlite_core_engine(tmp_path / "catalog.db")
    tracked = TrackingUnitOfWorkFactory(SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local"))
    source = UnitOfWorkObservingCatalogSource(tracked)
    application = ModelCatalogApplication(
        unit_of_work_factory=tracked,
        source=source,
    )
    try:
        refreshed = application.refresh_catalog()
        assert refreshed.revision == 1
        assert source.active_during_fetch == 0
    finally:
        application.close()
        engine.dispose()


def test_slower_concurrent_catalog_refresh_cannot_overwrite_newer_revision(tmp_path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "catalog.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    waiting_source = WaitingCatalogSource()
    fast_source = FakeCatalogSource()
    slow_application = ModelCatalogApplication(
        unit_of_work_factory=factory,
        source=waiting_source,
    )
    fast_application = ModelCatalogApplication(
        unit_of_work_factory=factory,
        source=fast_source,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            slow_result = executor.submit(slow_application.refresh_catalog)
            assert waiting_source.entered.wait(timeout=5)
            fast_result = fast_application.refresh_catalog()
            waiting_source.release.set()
            stale_result = slow_result.result(timeout=5)

        assert fast_result.revision == 1
        assert stale_result.revision == 1
        with factory() as unit_of_work:
            persisted = unit_of_work.model_catalog.get_catalog()
        assert persisted is not None
        assert persisted.revision == 1
    finally:
        waiting_source.release.set()
        slow_application.close()
        fast_application.close()
        engine.dispose()


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
