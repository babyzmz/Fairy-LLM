from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fairy_core.model_catalog.models import (
    ModelCatalogSnapshot,
    ModelSelectionMode,
    ModelSelectionPreference,
    ProviderAccount,
    ProviderCredentialStatus,
    baseline_catalog,
    merge_catalog_entries,
)
from fairy_core.model_catalog.ports import ModelCatalogSource, ModelCatalogSourceError
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory

Clock = Callable[[], datetime]
CredentialStatusResolver = Callable[[], ProviderCredentialStatus]


class ModelCatalogApplication:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        source: ModelCatalogSource | None = None,
        credential_status: CredentialStatusResolver | None = None,
        clock: Clock | None = None,
        cache_ttl: timedelta = timedelta(hours=6),
    ) -> None:
        if cache_ttl <= timedelta(0):
            raise ValueError("model catalog cache TTL must be positive")
        self._unit_of_work_factory = unit_of_work_factory
        self._source = source
        self._credential_status = credential_status or (
            lambda: ProviderCredentialStatus.UNAVAILABLE
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cache_ttl = cache_ttl

    def list_catalog(self) -> ModelCatalogSnapshot:
        with self._unit_of_work_factory() as unit_of_work:
            snapshot = unit_of_work.model_catalog.get_catalog()
        return snapshot or baseline_catalog(
            now=self._now(),
            credential_status=self._credential_status(),
        )

    def refresh_catalog(self) -> ModelCatalogSnapshot:
        with self._unit_of_work_factory() as unit_of_work:
            current = unit_of_work.model_catalog.get_catalog()
            expected_revision = current.revision if current is not None else 0
            try:
                if self._source is None:
                    raise ModelCatalogSourceError(
                        "MODEL_CATALOG_SOURCE_UNAVAILABLE",
                        credential_status=self._credential_status(),
                    )
                fetched = self._source.fetch()
                fetched_at = _aware(fetched.fetched_at)
                by_id = {entry.model_id: entry for entry in fetched.entries}
                if len(by_id) != len(fetched.entries):
                    raise ModelCatalogSourceError("MODEL_CATALOG_RESPONSE_INVALID")
                candidate = ModelCatalogSnapshot(
                    account=ProviderAccount(
                        account_id="openrouter-default",
                        provider_kind="openrouter",
                        display_name="OpenRouter",
                        credential_status=fetched.credential_status,
                    ),
                    entries=merge_catalog_entries(by_id),
                    fetched_at=fetched_at,
                    expires_at=fetched_at + self._cache_ttl,
                    revision=expected_revision,
                )
            except ModelCatalogSourceError as error:
                candidate = self._failure_snapshot(
                    current,
                    error_code=error.error_code,
                    credential_status=error.credential_status,
                )
            except Exception:
                candidate = self._failure_snapshot(
                    current,
                    error_code="MODEL_CATALOG_SOURCE_UNAVAILABLE",
                )
            saved = unit_of_work.model_catalog.replace_catalog(
                candidate,
                expected_revision=expected_revision,
            )
            unit_of_work.commit()
            return saved

    def get_selection(self) -> ModelSelectionPreference:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.model_catalog.get_selection()

    def update_selection(
        self,
        *,
        mode: ModelSelectionMode,
        model_id: str | None,
        allow_free_fallback: bool,
        zero_data_retention: bool,
        expected_revision: int,
        idempotency_key: str,
    ) -> ModelSelectionPreference:
        with self._unit_of_work_factory() as unit_of_work:
            selection = unit_of_work.model_catalog.update_selection(
                mode=mode,
                model_id=model_id,
                allow_free_fallback=allow_free_fallback,
                zero_data_retention=zero_data_retention,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
            )
            unit_of_work.commit()
            return selection

    def close(self) -> None:
        if self._source is not None:
            self._source.close()

    def _failure_snapshot(
        self,
        current: ModelCatalogSnapshot | None,
        *,
        error_code: str,
        credential_status: ProviderCredentialStatus | None = None,
    ) -> ModelCatalogSnapshot:
        status = credential_status or self._credential_status()
        if current is None:
            return baseline_catalog(
                now=self._now(),
                credential_status=status,
                error_code=error_code,
            )
        return current.with_error(
            error_code=error_code,
            credential_status=credential_status,
        )

    def _now(self) -> datetime:
        return _aware(self._clock())


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("model catalog clock must be timezone-aware")
    return value.astimezone(UTC)


__all__ = ["ModelCatalogApplication"]
