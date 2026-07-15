from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, RowMapping

from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.model_catalog.models import (
    ModelAvailability,
    ModelCatalogEntry,
    ModelCatalogSnapshot,
    ModelCategory,
    ModelEndpointKind,
    ModelPrice,
    ModelSelectionMode,
    ModelSelectionPreference,
    ProviderAccount,
    ProviderCredentialStatus,
)
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.storage.schema import (
    model_catalogs,
    model_selection_updates,
    model_selections,
)

Clock = Callable[[], datetime]


class SqlAlchemyModelCatalogRepository:
    def __init__(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        clock: Clock | None = None,
    ) -> None:
        self._connection = connection
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._clock = clock or (lambda: datetime.now(UTC))

    def get_catalog(self) -> ModelCatalogSnapshot | None:
        row = (
            self._connection.execute(
                select(model_catalogs).where(model_catalogs.c.tenant_id == self._tenant_id)
            )
            .mappings()
            .one_or_none()
        )
        return _catalog_from_row(row) if row is not None else None

    def replace_catalog(
        self,
        snapshot: ModelCatalogSnapshot,
        *,
        expected_revision: int,
    ) -> ModelCatalogSnapshot:
        if expected_revision < 0:
            raise ValueError("expected model catalog revision cannot be negative")
        saved = replace(snapshot, revision=expected_revision + 1)
        values = {
            "tenant_id": self._tenant_id,
            "account_id": saved.account.account_id,
            "provider_kind": saved.account.provider_kind,
            "display_name": saved.account.display_name,
            "credential_status": saved.account.credential_status.value,
            "entries": [_entry_to_record(entry) for entry in saved.entries],
            "fetched_at": saved.fetched_at,
            "expires_at": saved.expires_at,
            "revision": saved.revision,
            "last_error_code": saved.last_error_code,
        }
        if expected_revision == 0:
            statement = (
                postgresql_insert(model_catalogs)
                if self._connection.dialect.name == "postgresql"
                else sqlite_insert(model_catalogs)
            )
            inserted = self._connection.execute(
                statement.values(**values).on_conflict_do_nothing(
                    index_elements=[model_catalogs.c.tenant_id]
                )
            )
            if inserted.rowcount == 1:
                return saved
        changed = self._connection.execute(
            update(model_catalogs)
            .where(
                model_catalogs.c.tenant_id == self._tenant_id,
                model_catalogs.c.revision == expected_revision,
            )
            .values(**{key: value for key, value in values.items() if key != "tenant_id"})
        )
        if changed.rowcount != 1:
            raise VersionConflictError("model catalog revision changed")
        return saved

    def get_selection(self) -> ModelSelectionPreference:
        row = (
            self._connection.execute(
                select(model_selections).where(model_selections.c.tenant_id == self._tenant_id)
            )
            .mappings()
            .one_or_none()
        )
        return (
            ModelSelectionPreference.defaults(now=self._now())
            if row is None
            else _selection_from_row(row)
        )

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
        if expected_revision < 0:
            raise ValueError("expected model selection revision cannot be negative")
        canonical_key = _idempotency_key(idempotency_key)
        fingerprint = _request_fingerprint(
            mode=mode,
            model_id=model_id,
            allow_free_fallback=allow_free_fallback,
            zero_data_retention=zero_data_retention,
            expected_revision=expected_revision,
        )
        existing = self._selection_request(canonical_key)
        if existing is not None:
            return _replayed_selection(existing, fingerprint=fingerprint)

        result = ModelSelectionPreference(
            mode=mode,
            model_id=model_id,
            allow_free_fallback=allow_free_fallback,
            zero_data_retention=zero_data_retention,
            revision=expected_revision + 1,
            updated_at=self._now(),
        )
        statement = (
            postgresql_insert(model_selection_updates)
            if self._connection.dialect.name == "postgresql"
            else sqlite_insert(model_selection_updates)
        )
        reserved = self._connection.execute(
            statement.values(
                tenant_id=self._tenant_id,
                idempotency_key=canonical_key,
                request_fingerprint=fingerprint,
                mode=result.mode.value,
                model_id=result.model_id,
                allow_free_fallback=result.allow_free_fallback,
                zero_data_retention=result.zero_data_retention,
                expected_revision=expected_revision,
                result_revision=result.revision,
                result_updated_at=result.updated_at,
            ).on_conflict_do_nothing(
                index_elements=[
                    model_selection_updates.c.tenant_id,
                    model_selection_updates.c.idempotency_key,
                ]
            )
        )
        if reserved.rowcount != 1:
            replay = self._selection_request(canonical_key)
            if replay is None:
                raise VersionConflictError("model selection request could not be reserved")
            return _replayed_selection(replay, fingerprint=fingerprint)

        values = {
            "tenant_id": self._tenant_id,
            "mode": result.mode.value,
            "model_id": result.model_id,
            "allow_free_fallback": result.allow_free_fallback,
            "zero_data_retention": result.zero_data_retention,
            "revision": result.revision,
            "updated_at": result.updated_at,
        }
        if expected_revision == 0:
            statement = (
                postgresql_insert(model_selections)
                if self._connection.dialect.name == "postgresql"
                else sqlite_insert(model_selections)
            )
            inserted = self._connection.execute(
                statement.values(**values).on_conflict_do_nothing(
                    index_elements=[model_selections.c.tenant_id]
                )
            )
            if inserted.rowcount == 1:
                return result

        changed = self._connection.execute(
            update(model_selections)
            .where(
                model_selections.c.tenant_id == self._tenant_id,
                model_selections.c.revision == expected_revision,
            )
            .values(**{key: value for key, value in values.items() if key != "tenant_id"})
        )
        if changed.rowcount != 1:
            raise VersionConflictError("model selection revision changed")
        return result

    def _selection_request(self, idempotency_key: str) -> RowMapping | None:
        return (
            self._connection.execute(
                select(model_selection_updates).where(
                    model_selection_updates.c.tenant_id == self._tenant_id,
                    model_selection_updates.c.idempotency_key == idempotency_key,
                )
            )
            .mappings()
            .one_or_none()
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("model catalog clock must be timezone-aware")
        return value.astimezone(UTC)


def _entry_to_record(entry: ModelCatalogEntry) -> dict[str, object]:
    return {
        "model_id": entry.model_id,
        "display_name": entry.display_name,
        "category": entry.category.value,
        "endpoint_kind": entry.endpoint_kind.value,
        "description": entry.description,
        "paid": entry.paid,
        "availability": entry.availability.value,
        "unavailable_reason": entry.unavailable_reason,
        "input_modalities": list(entry.input_modalities),
        "output_modalities": list(entry.output_modalities),
        "context_length": entry.context_length,
        "max_output_tokens": entry.max_output_tokens,
        "supports_tools": entry.supports_tools,
        "supports_structured_output": entry.supports_structured_output,
        "supports_streaming": entry.supports_streaming,
        "supported_resolutions": list(entry.supported_resolutions),
        "supported_aspect_ratios": list(entry.supported_aspect_ratios),
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


def _catalog_from_row(row: RowMapping) -> ModelCatalogSnapshot:
    entries = tuple(_entry_from_record(record) for record in row["entries"])
    return ModelCatalogSnapshot(
        account=ProviderAccount(
            account_id=str(row["account_id"]),
            provider_kind=str(row["provider_kind"]),
            display_name=str(row["display_name"]),
            credential_status=ProviderCredentialStatus(row["credential_status"]),
        ),
        entries=entries,
        fetched_at=_aware(row["fetched_at"]),
        expires_at=_aware(row["expires_at"]),
        revision=int(row["revision"]),
        last_error_code=row["last_error_code"],
    )


def _entry_from_record(record: object) -> ModelCatalogEntry:
    if not isinstance(record, dict):
        raise ValueError("stored model catalog entry is invalid")
    return ModelCatalogEntry(
        model_id=str(record["model_id"]),
        display_name=str(record["display_name"]),
        category=ModelCategory(record["category"]),
        endpoint_kind=ModelEndpointKind(record["endpoint_kind"]),
        description=str(record["description"]),
        paid=bool(record["paid"]),
        availability=ModelAvailability(record["availability"]),
        unavailable_reason=(
            str(record["unavailable_reason"])
            if record.get("unavailable_reason") is not None
            else None
        ),
        input_modalities=tuple(str(value) for value in record.get("input_modalities", [])),
        output_modalities=tuple(str(value) for value in record.get("output_modalities", [])),
        context_length=(
            int(record["context_length"]) if record.get("context_length") is not None else None
        ),
        max_output_tokens=(
            int(record["max_output_tokens"])
            if record.get("max_output_tokens") is not None
            else None
        ),
        supports_tools=bool(record.get("supports_tools", False)),
        supports_structured_output=bool(record.get("supports_structured_output", False)),
        supports_streaming=bool(record.get("supports_streaming", False)),
        supported_resolutions=tuple(
            str(value) for value in record.get("supported_resolutions", [])
        ),
        supported_aspect_ratios=tuple(
            str(value) for value in record.get("supported_aspect_ratios", [])
        ),
        prices=tuple(
            ModelPrice(
                billable=str(price["billable"]),
                unit=str(price["unit"]),
                cost_usd=str(price["cost_usd"]),
                variant=str(price["variant"]) if price.get("variant") is not None else None,
            )
            for price in record.get("prices", [])
            if isinstance(price, dict)
        ),
    )


def _selection_from_row(row: RowMapping) -> ModelSelectionPreference:
    return ModelSelectionPreference(
        mode=ModelSelectionMode(row["mode"]),
        model_id=row["model_id"],
        allow_free_fallback=bool(row["allow_free_fallback"]),
        zero_data_retention=bool(row["zero_data_retention"]),
        revision=int(row["revision"]),
        updated_at=_aware(row["updated_at"]),
    )


def _replayed_selection(row: RowMapping, *, fingerprint: str) -> ModelSelectionPreference:
    if row["request_fingerprint"] != fingerprint:
        raise IdempotencyConflictError("model selection idempotency key was reused")
    return ModelSelectionPreference(
        mode=ModelSelectionMode(row["mode"]),
        model_id=row["model_id"],
        allow_free_fallback=bool(row["allow_free_fallback"]),
        zero_data_retention=bool(row["zero_data_retention"]),
        revision=int(row["result_revision"]),
        updated_at=_aware(row["result_updated_at"]),
    )


def _request_fingerprint(
    *,
    mode: ModelSelectionMode,
    model_id: str | None,
    allow_free_fallback: bool,
    zero_data_retention: bool,
    expected_revision: int,
) -> str:
    payload = json.dumps(
        {
            "mode": mode.value,
            "model_id": model_id,
            "allow_free_fallback": allow_free_fallback,
            "zero_data_retention": zero_data_retention,
            "expected_revision": expected_revision,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _idempotency_key(value: str) -> str:
    if value != value.strip() or not value or len(value) > 512:
        raise ValueError("model selection idempotency key is invalid")
    if any(ord(character) < 32 for character in value):
        raise ValueError("model selection idempotency key is invalid")
    return value


def _aware(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


__all__ = ["SqlAlchemyModelCatalogRepository"]
