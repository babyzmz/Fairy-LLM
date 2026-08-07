"""Bind Assistant schedules to an operation mode.

Revision ID: 20260807_0050
Revises: 20260807_0049
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0050"
down_revision: str | Sequence[str] | None = "20260807_0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEDULES = "core_assistant_schedules"
OCCURRENCES = "core_assistant_schedule_occurrences"
CHECK = "ck_core_assistant_schedules_operation_mode"
IDEMPOTENCY_INDEX = "uq_core_assistant_occurrences_idempotency"


def upgrade() -> None:
    op.add_column(
        SCHEDULES,
        sa.Column(
            "operation_mode",
            sa.String(32),
            nullable=False,
            server_default="answer",
        ),
    )
    op.alter_column(SCHEDULES, "operation_mode", server_default=None)
    op.create_check_constraint(
        CHECK,
        SCHEDULES,
        "operation_mode IN ('answer','continue_current_chat_draft','create_new_version')",
    )
    op.add_column(OCCURRENCES, sa.Column("idempotency_key", sa.String(512)))
    op.create_index(
        IDEMPOTENCY_INDEX,
        OCCURRENCES,
        ["tenant_id", "schedule_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(IDEMPOTENCY_INDEX, table_name=OCCURRENCES)
    op.drop_column(OCCURRENCES, "idempotency_key")
    op.drop_constraint(CHECK, SCHEDULES, type_="check")
    op.drop_column(SCHEDULES, "operation_mode")
