"""Add clarification idempotency and waiting-for-input states.

Revision ID: 20260808_0052
Revises: 20260808_0051
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0052"
down_revision: str | Sequence[str] | None = "20260808_0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "core_assistant_request_interpretations",
        sa.Column("idempotency_key", sa.String(512)),
    )
    op.execute(
        "UPDATE core_assistant_request_interpretations "
        "SET idempotency_key = 'legacy:' || revision"
    )
    op.alter_column(
        "core_assistant_request_interpretations",
        "idempotency_key",
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_core_assistant_interpretations_idempotency",
        "core_assistant_request_interpretations",
        ["tenant_id", "turn_id", "idempotency_key"],
    )
    _replace_check(
        "core_assistant_turns",
        "ck_core_assistant_turns_status",
        "status IN ('created','running','waiting_for_tool','waiting_for_input',"
        "'completed','cancelled','failed')",
    )
    _replace_check(
        "core_workflow_runs",
        "ck_core_workflow_runs_status",
        "status IN ('queued','running','waiting_for_approval','waiting_for_input','paused',"
        "'completed','cancelled','failed')",
    )
    _replace_check(
        "core_workflow_nodes",
        "ck_core_workflow_nodes_status",
        "status IN ('pending','ready','running','waiting_for_approval','waiting_for_input',"
        "'succeeded','failed','cancelled','skipped','superseded')",
    )


def downgrade() -> None:
    _replace_check(
        "core_assistant_turns",
        "ck_core_assistant_turns_status",
        "status IN ('created','running','waiting_for_tool','completed','cancelled','failed')",
    )
    _replace_check(
        "core_workflow_runs",
        "ck_core_workflow_runs_status",
        "status IN ('queued','running','waiting_for_approval','paused','completed','cancelled',"
        "'failed')",
    )
    _replace_check(
        "core_workflow_nodes",
        "ck_core_workflow_nodes_status",
        "status IN ('pending','ready','running','waiting_for_approval','succeeded','failed',"
        "'cancelled','skipped','superseded')",
    )
    op.drop_constraint(
        "uq_core_assistant_interpretations_idempotency",
        "core_assistant_request_interpretations",
        type_="unique",
    )
    op.drop_column("core_assistant_request_interpretations", "idempotency_key")


def _replace_check(table: str, name: str, expression: str) -> None:
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, expression)
