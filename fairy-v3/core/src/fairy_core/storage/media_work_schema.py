from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
)

from fairy_core.persistence.tenant import TENANT_ID_LENGTH
from fairy_core.storage.types import UTCDateTime


def build_media_work_table(metadata: MetaData, media_jobs: Table) -> Table:
    table = Table(
        "core_media_generation_work",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), nullable=False),
        Column("job_id", String(36), nullable=False),
        Column("available_at", UTCDateTime()),
        Column("lease_owner", String(128)),
        Column("lease_until", UTCDateTime()),
        Column("lease_fence", BigInteger, nullable=False),
        Column("attempts", BigInteger, nullable=False),
        Column("last_error_code", String(128)),
        Column("updated_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", "job_id", name="pk_core_media_generation_work"),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            [media_jobs.c.tenant_id, media_jobs.c.id],
            name="fk_core_media_generation_work_job",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "lease_fence >= 0 AND attempts >= 0",
            name="ck_core_media_generation_work_counters",
        ),
        CheckConstraint(
            "(lease_owner IS NULL) = (lease_until IS NULL)",
            name="ck_core_media_generation_work_lease_pair",
        ),
        CheckConstraint(
            "lease_owner IS NULL OR available_at IS NOT NULL",
            name="ck_core_media_generation_work_active_pending",
        ),
    )
    Index(
        "ix_core_media_generation_work_claim",
        table.c.available_at,
        table.c.lease_until,
        table.c.job_id,
    )
    return table


__all__ = ["build_media_work_table"]
