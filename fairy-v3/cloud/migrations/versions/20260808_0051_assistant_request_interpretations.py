"""Add durable Assistant request interpretations.

Revision ID: 20260808_0051
Revises: 20260807_0050
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260808_0051"
down_revision: str | Sequence[str] | None = "20260807_0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TURNS = "core_assistant_turns"
INTERPRETATIONS = "core_assistant_request_interpretations"


def upgrade() -> None:
    op.add_column(TURNS, sa.Column("active_interpretation_revision", sa.BigInteger()))
    op.create_check_constraint(
        "ck_core_assistant_turns_interpretation_revision",
        TURNS,
        "active_interpretation_revision IS NULL OR active_interpretation_revision > 0",
    )
    op.create_table(
        INTERPRETATIONS,
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("turn_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", sa.String(36), nullable=False),
        sa.Column("source_message_sha256", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("normalized_goal", sa.String(4_000), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("objectives", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("targets", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("constraints", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("deliverable", sa.String(2_000)),
        sa.Column("evidence_requirements", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("missing_information", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("public_summary", sa.String(240), nullable=False),
        sa.Column("clarification_question", sa.String(1_000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id", "id", name="pk_core_assistant_request_interpretations"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "turn_id",
            "revision",
            name="uq_core_assistant_interpretations_revision",
        ),
        sa.CheckConstraint("revision > 0", name="ck_core_assistant_interpretations_revision"),
        sa.CheckConstraint(
            "schema_version > 0", name="ck_core_assistant_interpretations_schema"
        ),
        sa.CheckConstraint(
            "length(source_message_sha256) = 64 "
            "AND source_message_sha256 = lower(source_message_sha256)",
            name="ck_core_assistant_interpretations_source_hash",
        ),
        sa.CheckConstraint(
            "action IN ('answer','explain','review','change','create','run','browse',"
            "'generate','schedule','manage')",
            name="ck_core_assistant_interpretations_action",
        ),
        sa.CheckConstraint(
            "confidence IN ('low','medium','high')",
            name="ck_core_assistant_interpretations_confidence",
        ),
        sa.CheckConstraint(
            "disposition IN ('ready','assumed','clarification_required')",
            name="ck_core_assistant_interpretations_disposition",
        ),
        sa.CheckConstraint(
            "(disposition = 'clarification_required' AND clarification_question IS NOT NULL) OR "
            "(disposition != 'clarification_required' AND clarification_question IS NULL)",
            name="ck_core_assistant_interpretations_clarification",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "turn_id"],
            ["core_assistant_turns.tenant_id", "core_assistant_turns.id"],
            name="fk_core_assistant_interpretations_turn",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_message_id"],
            ["core_assistant_messages.tenant_id", "core_assistant_messages.id"],
            name="fk_core_assistant_interpretations_source_message",
            ondelete="CASCADE",
        ),
    )
    op.execute(
        "ALTER TABLE core_assistant_request_interpretations ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "CREATE POLICY core_assistant_request_interpretations_tenant_isolation "
        "ON core_assistant_request_interpretations USING "
        "(tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.drop_table(INTERPRETATIONS)
    op.drop_constraint(
        "ck_core_assistant_turns_interpretation_revision", TURNS, type_="check"
    )
    op.drop_column(TURNS, "active_interpretation_revision")
