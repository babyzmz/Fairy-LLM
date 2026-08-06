"""Bind Assistant Turns to one durable execution engine.

Revision ID: 20260807_0046
Revises: 20260807_0045
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0046"
down_revision: str | Sequence[str] | None = "20260807_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TURNS = "core_assistant_turns"
RUNS = "core_workflow_runs"


def upgrade() -> None:
    op.add_column(TURNS, sa.Column("workflow_run_id", sa.String(36), nullable=True))
    op.add_column(
        TURNS,
        sa.Column(
            "execution_engine_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.alter_column(TURNS, "execution_engine_version", server_default=None)
    op.create_unique_constraint(
        "uq_core_assistant_turns_workflow_run",
        TURNS,
        ["tenant_id", "workflow_run_id"],
    )
    op.create_check_constraint(
        "ck_core_assistant_turns_execution_engine",
        TURNS,
        "execution_engine_version >= 1",
    )
    op.create_foreign_key(
        "fk_core_assistant_turns_workflow_run",
        TURNS,
        RUNS,
        ["tenant_id", "workflow_run_id"],
        ["tenant_id", "id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_core_assistant_turns_workflow_run", TURNS, type_="foreignkey")
    op.drop_constraint("ck_core_assistant_turns_execution_engine", TURNS, type_="check")
    op.drop_constraint("uq_core_assistant_turns_workflow_run", TURNS, type_="unique")
    op.drop_column(TURNS, "execution_engine_version")
    op.drop_column(TURNS, "workflow_run_id")
