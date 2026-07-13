"""Persist model-provider attempts and their safe failure categories."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0019"
down_revision: str | Sequence[str] | None = "20260712_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "core_assistant_provider_attempts",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("turn_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("model_round", sa.BigInteger(), nullable=False),
        sa.Column("attempt_number", sa.BigInteger(), nullable=False),
        sa.Column("profile_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_category", sa.String(32)),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint(
            "tenant_id", "id", name="pk_core_assistant_provider_attempts"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "turn_id",
            "model_round",
            "attempt_number",
            name="uq_core_assistant_provider_attempts_turn_round_number",
        ),
        sa.CheckConstraint("model_round > 0", name="ck_core_provider_attempts_model_round"),
        sa.CheckConstraint("attempt_number > 0", name="ck_core_provider_attempts_number"),
        sa.CheckConstraint(
            "status IN ('started', 'succeeded', 'failed')",
            name="ck_core_provider_attempts_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "turn_id", "task_id"],
            [
                "core_assistant_turns.tenant_id",
                "core_assistant_turns.id",
                "core_assistant_turns.task_id",
            ],
            name="fk_core_provider_attempts_turn_task",
        ),
    )
    op.create_index(
        "ix_core_provider_attempts_tenant_turn",
        "core_assistant_provider_attempts",
        ["tenant_id", "turn_id", "model_round", "attempt_number"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_core_provider_attempts_tenant_turn",
        table_name="core_assistant_provider_attempts",
    )
    op.drop_table("core_assistant_provider_attempts")
