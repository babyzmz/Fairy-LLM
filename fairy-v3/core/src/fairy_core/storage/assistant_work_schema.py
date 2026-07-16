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

_ID_LENGTH = 36


def build_assistant_work_table(metadata: MetaData, assistant_turns: Table) -> Table:
    table = Table(
        "core_assistant_turn_work",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), nullable=False),
        Column("turn_id", String(_ID_LENGTH), nullable=False),
        Column("request_revision", BigInteger, nullable=False),
        Column("completed_revision", BigInteger, nullable=False),
        Column("requested_at", UTCDateTime()),
        Column("lease_owner", String(128)),
        Column("lease_until", UTCDateTime()),
        Column("lease_fence", BigInteger, nullable=False),
        Column("attempts", BigInteger, nullable=False),
        Column("last_error_code", String(128)),
        Column("updated_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", "turn_id", name="pk_core_assistant_turn_work"),
        ForeignKeyConstraint(
            ["tenant_id", "turn_id"],
            [assistant_turns.c.tenant_id, assistant_turns.c.id],
            name="fk_core_assistant_turn_work_turn",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "request_revision >= completed_revision AND completed_revision >= 0",
            name="ck_core_assistant_turn_work_revisions",
        ),
        CheckConstraint(
            "lease_fence >= 0 AND attempts >= 0",
            name="ck_core_assistant_turn_work_counters",
        ),
        CheckConstraint(
            "(request_revision > completed_revision) = (requested_at IS NOT NULL)",
            name="ck_core_assistant_turn_work_pending",
        ),
        CheckConstraint(
            "(lease_owner IS NULL) = (lease_until IS NULL)",
            name="ck_core_assistant_turn_work_lease_pair",
        ),
        CheckConstraint(
            "lease_owner IS NULL OR request_revision > completed_revision",
            name="ck_core_assistant_turn_work_active_pending",
        ),
    )
    Index(
        "ix_core_assistant_turn_work_claim",
        table.c.requested_at,
        table.c.lease_until,
        table.c.turn_id,
    )
    return table


__all__ = ["build_assistant_work_table"]
