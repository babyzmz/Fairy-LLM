"""Remove the superseded Assistant Turn work queue.

Revision ID: 20260807_0047
Revises: 20260807_0046
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0047"
down_revision: str | Sequence[str] | None = "20260807_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "core_assistant_turn_work"
TURNS = "core_assistant_turns"


def upgrade() -> None:
    op.execute(
        sa.text(
            f"""
            DO $fairy$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM {TURNS}
                    WHERE status NOT IN ('completed', 'cancelled', 'failed')
                      AND (workflow_run_id IS NULL OR execution_engine_version < 2)
                ) THEN
                    RAISE EXCEPTION
                        'Non-terminal pre-Workflow Assistant Turns block this Cloud upgrade';
                END IF;
            END
            $fairy$
            """
        )
    )
    op.execute(sa.text(f'DROP POLICY IF EXISTS "tenant_isolation_{TABLE}" ON "{TABLE}"'))
    op.execute(sa.text(f'ALTER TABLE "{TABLE}" DISABLE ROW LEVEL SECURITY'))
    op.drop_index("ix_core_assistant_turn_work_claim", table_name=TABLE)
    op.drop_table(TABLE)


def downgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("turn_id", sa.String(36), nullable=False),
        sa.Column("request_revision", sa.BigInteger(), nullable=False),
        sa.Column("completed_revision", sa.BigInteger(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_fence", sa.BigInteger(), nullable=False),
        sa.Column("attempts", sa.BigInteger(), nullable=False),
        sa.Column("last_error_code", sa.String(128)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "turn_id", name="pk_core_assistant_turn_work"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "turn_id"],
            ["core_assistant_turns.tenant_id", "core_assistant_turns.id"],
            name="fk_core_assistant_turn_work_turn",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "request_revision >= completed_revision AND completed_revision >= 0",
            name="ck_core_assistant_turn_work_revisions",
        ),
        sa.CheckConstraint(
            "lease_fence >= 0 AND attempts >= 0",
            name="ck_core_assistant_turn_work_counters",
        ),
        sa.CheckConstraint(
            "(request_revision > completed_revision) = (requested_at IS NOT NULL)",
            name="ck_core_assistant_turn_work_pending",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_until IS NULL)",
            name="ck_core_assistant_turn_work_lease_pair",
        ),
        sa.CheckConstraint(
            "lease_owner IS NULL OR request_revision > completed_revision",
            name="ck_core_assistant_turn_work_active_pending",
        ),
    )
    op.create_index(
        "ix_core_assistant_turn_work_claim",
        TABLE,
        ["requested_at", "lease_until", "turn_id"],
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
