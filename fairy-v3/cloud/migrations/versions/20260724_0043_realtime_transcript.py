"""Add local reviewable realtime transcript entries.

Revision ID: 20260724_0043
Revises: 20260723_0042
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_0043"
down_revision: str | Sequence[str] | None = "20260723_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "core_realtime_transcript_entries"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("speaker", sa.String(16), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id", "id", name="pk_core_realtime_transcript_entries"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "session_id",
            "sequence",
            name="uq_core_realtime_transcript_sequence",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["core_realtime_sessions.tenant_id", "core_realtime_sessions.id"],
            name="fk_core_realtime_transcript_session",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "sequence >= 1", name="ck_core_realtime_transcript_sequence"
        ),
        sa.CheckConstraint(
            "speaker IN ('user', 'assistant')",
            name="ck_core_realtime_transcript_speaker",
        ),
    )
    op.create_index(
        "ix_core_realtime_transcript_conversation",
        TABLE,
        ["tenant_id", "conversation_id", "created_at"],
    )
    _enable_rls(TABLE)


def downgrade() -> None:
    op.execute(sa.text(f'DROP POLICY IF EXISTS "tenant_isolation_{TABLE}" ON "{TABLE}"'))
    op.execute(sa.text(f'ALTER TABLE "{TABLE}" DISABLE ROW LEVEL SECURITY'))
    op.drop_index(
        "ix_core_realtime_transcript_conversation", table_name=TABLE
    )
    op.drop_table(TABLE)


def _enable_rls(table_name: str) -> None:
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
    op.execute(sa.text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            f'CREATE POLICY "tenant_isolation_{table_name}" ON "{table_name}" '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    )
