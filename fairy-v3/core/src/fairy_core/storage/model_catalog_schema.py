from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
)

from fairy_core.persistence.tenant import TENANT_ID_LENGTH
from fairy_core.storage.types import UTCDateTime


def build_model_catalog_tables(metadata: MetaData) -> tuple[Table, Table, Table]:
    catalogs = Table(
        "core_model_catalogs",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("account_id", String(128), nullable=False),
        Column("provider_kind", String(32), nullable=False),
        Column("display_name", String(255), nullable=False),
        Column("credential_status", String(32), nullable=False),
        Column("entries", JSON, nullable=False),
        Column("fetched_at", UTCDateTime(), nullable=False),
        Column("expires_at", UTCDateTime(), nullable=False),
        Column("revision", BigInteger, nullable=False),
        Column("last_error_code", String(128)),
        PrimaryKeyConstraint("tenant_id", name="pk_core_model_catalogs"),
        CheckConstraint(
            "provider_kind = 'openrouter'",
            name="ck_core_model_catalogs_provider_kind",
        ),
        CheckConstraint(
            "credential_status IN ('configured', 'invalid', 'unavailable')",
            name="ck_core_model_catalogs_credential_status",
        ),
        CheckConstraint("revision >= 1", name="ck_core_model_catalogs_revision"),
        CheckConstraint("expires_at > fetched_at", name="ck_core_model_catalogs_expiry"),
    )
    selections = Table(
        "core_model_selections",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("mode", String(16), nullable=False),
        Column("model_id", String(255)),
        Column("allow_free_fallback", Boolean, nullable=False),
        Column("zero_data_retention", Boolean, nullable=False),
        Column("revision", BigInteger, nullable=False),
        Column("updated_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", name="pk_core_model_selections"),
        CheckConstraint(
            "mode IN ('auto', 'manual')",
            name="ck_core_model_selections_mode",
        ),
        CheckConstraint(
            "(mode = 'auto' AND model_id IS NULL) OR (mode = 'manual' AND model_id IS NOT NULL)",
            name="ck_core_model_selections_model",
        ),
        CheckConstraint("revision >= 1", name="ck_core_model_selections_revision"),
    )
    selection_updates = Table(
        "core_model_selection_updates",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("idempotency_key", String(512), primary_key=True),
        Column("request_fingerprint", String(64), nullable=False),
        Column("mode", String(16), nullable=False),
        Column("model_id", String(255)),
        Column("allow_free_fallback", Boolean, nullable=False),
        Column("zero_data_retention", Boolean, nullable=False),
        Column("expected_revision", BigInteger, nullable=False),
        Column("result_revision", BigInteger, nullable=False),
        Column("result_updated_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint(
            "tenant_id",
            "idempotency_key",
            name="pk_core_model_selection_updates",
        ),
        CheckConstraint(
            "mode IN ('auto', 'manual')",
            name="ck_core_model_selection_updates_mode",
        ),
        CheckConstraint(
            "(mode = 'auto' AND model_id IS NULL) OR (mode = 'manual' AND model_id IS NOT NULL)",
            name="ck_core_model_selection_updates_model",
        ),
        CheckConstraint(
            "expected_revision >= 0 AND result_revision = expected_revision + 1",
            name="ck_core_model_selection_updates_revision",
        ),
    )
    return catalogs, selections, selection_updates


__all__ = ["build_model_catalog_tables"]
