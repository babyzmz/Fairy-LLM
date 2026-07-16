"""Add the durable Media generation work queue.

Revision ID: 20260716_0035
Revises: 20260716_0034
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0035"
down_revision: str | Sequence[str] | None = "20260716_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "core_media_generation_work"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_fence", sa.BigInteger(), nullable=False),
        sa.Column("attempts", sa.BigInteger(), nullable=False),
        sa.Column("last_error_code", sa.String(128)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "job_id", name="pk_core_media_generation_work"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["core_media_generation_jobs.tenant_id", "core_media_generation_jobs.id"],
            name="fk_core_media_generation_work_job",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "lease_fence >= 0 AND attempts >= 0",
            name="ck_core_media_generation_work_counters",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_until IS NULL)",
            name="ck_core_media_generation_work_lease_pair",
        ),
        sa.CheckConstraint(
            "lease_owner IS NULL OR available_at IS NOT NULL",
            name="ck_core_media_generation_work_active_pending",
        ),
    )
    op.create_index(
        "ix_core_media_generation_work_claim",
        TABLE,
        ["available_at", "lease_until", "job_id"],
    )
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
    op.execute(sa.text(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            f'CREATE POLICY "tenant_isolation_{TABLE}" ON "{TABLE}" '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    )


def downgrade() -> None:
    op.execute(sa.text(f'DROP POLICY IF EXISTS "tenant_isolation_{TABLE}" ON "{TABLE}"'))
    op.execute(sa.text(f'ALTER TABLE "{TABLE}" DISABLE ROW LEVEL SECURITY'))
    op.drop_index("ix_core_media_generation_work_claim", table_name=TABLE)
    op.drop_table(TABLE)
