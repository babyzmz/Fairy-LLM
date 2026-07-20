from __future__ import annotations

from sqlalchemy import (
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


def build_memory_settings_tables(metadata: MetaData) -> tuple[Table, Table]:
    settings = Table(
        "core_memory_settings",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("enabled", Boolean, nullable=False),
        Column("retention_days", BigInteger, nullable=False),
        Column("export_to_obsidian", Boolean, nullable=False),
        Column("sync_normalized_content", Boolean, nullable=False),
        Column("revision", BigInteger, nullable=False),
        Column("updated_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", name="pk_core_memory_settings"),
        CheckConstraint(
            "retention_days BETWEEN 1 AND 3650",
            name="ck_core_memory_settings_retention",
        ),
        CheckConstraint("revision >= 1", name="ck_core_memory_settings_revision"),
    )
    updates = Table(
        "core_memory_setting_updates",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("idempotency_key", String(512), primary_key=True),
        Column("request_fingerprint", String(64), nullable=False),
        Column("enabled", Boolean, nullable=False),
        Column("retention_days", BigInteger, nullable=False),
        Column("export_to_obsidian", Boolean, nullable=False),
        Column("sync_normalized_content", Boolean, nullable=False),
        Column("expected_revision", BigInteger, nullable=False),
        Column("result_revision", BigInteger, nullable=False),
        Column("result_updated_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint(
            "tenant_id",
            "idempotency_key",
            name="pk_core_memory_setting_updates",
        ),
        CheckConstraint(
            "retention_days BETWEEN 1 AND 3650",
            name="ck_core_memory_setting_updates_retention",
        ),
        CheckConstraint(
            "expected_revision >= 0 AND result_revision = expected_revision + 1",
            name="ck_core_memory_setting_updates_revision",
        ),
    )
    return settings, updates


__all__ = ["build_memory_settings_tables"]
