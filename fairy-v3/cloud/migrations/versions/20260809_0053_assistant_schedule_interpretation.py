"""Add bounded Assistant schedule interpretation metadata.

Revision ID: 20260809_0053
Revises: 20260808_0052
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_0053"
down_revision: str | Sequence[str] | None = "20260808_0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEDULES = "core_assistant_schedules"


def upgrade() -> None:
    op.add_column(SCHEDULES, sa.Column("interpretation_action", sa.String(32)))
    op.add_column(SCHEDULES, sa.Column("interpretation_summary", sa.String(240)))
    op.add_column(SCHEDULES, sa.Column("instruction_sha256", sa.String(64)))
    op.create_check_constraint(
        "ck_core_assistant_schedules_interpretation_pair",
        SCHEDULES,
        "(interpretation_action IS NULL) = (interpretation_summary IS NULL) "
        "AND (interpretation_action IS NULL) = (instruction_sha256 IS NULL)",
    )
    op.create_check_constraint(
        "ck_core_assistant_schedules_interpretation_action",
        SCHEDULES,
        "interpretation_action IS NULL OR interpretation_action IN "
        "('answer','explain','review','change','create','run','browse','generate',"
        "'schedule','manage')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_core_assistant_schedules_interpretation_action",
        SCHEDULES,
        type_="check",
    )
    op.drop_constraint(
        "ck_core_assistant_schedules_interpretation_pair",
        SCHEDULES,
        type_="check",
    )
    op.drop_column(SCHEDULES, "instruction_sha256")
    op.drop_column(SCHEDULES, "interpretation_summary")
    op.drop_column(SCHEDULES, "interpretation_action")
