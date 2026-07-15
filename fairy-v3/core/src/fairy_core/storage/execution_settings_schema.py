from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
)

from fairy_core.persistence.tenant import TENANT_ID_LENGTH
from fairy_core.storage.types import UTCDateTime


def build_execution_settings_tables(metadata: MetaData) -> tuple[Table, Table]:
    settings = Table(
        "core_execution_settings",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("profile", String(32), nullable=False),
        Column("capability_overrides", JSON, nullable=False),
        Column("revision", BigInteger, nullable=False),
        Column("updated_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", name="pk_core_execution_settings"),
        CheckConstraint(
            "profile IN ('observe', 'standard', 'autonomous')",
            name="ck_core_execution_settings_profile",
        ),
        CheckConstraint("revision >= 1", name="ck_core_execution_settings_revision"),
    )
    updates = Table(
        "core_execution_setting_updates",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("idempotency_key", String(512), primary_key=True),
        Column("request_fingerprint", String(64), nullable=False),
        Column("profile", String(32), nullable=False),
        Column("capability_overrides", JSON, nullable=False),
        Column("expected_revision", BigInteger, nullable=False),
        Column("result_revision", BigInteger, nullable=False),
        Column("result_updated_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint(
            "tenant_id",
            "idempotency_key",
            name="pk_core_execution_setting_updates",
        ),
        CheckConstraint(
            "profile IN ('observe', 'standard', 'autonomous')",
            name="ck_core_execution_setting_updates_profile",
        ),
        CheckConstraint(
            "expected_revision >= 0 AND result_revision = expected_revision + 1",
            name="ck_core_execution_setting_updates_revision",
        ),
    )
    return settings, updates


__all__ = ["build_execution_settings_tables"]
