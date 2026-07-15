"""Add assistant selection snapshots, routing decisions, and provider accounting.

Revision ID: 20260715_0030
Revises: 20260715_0029
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_0030"
down_revision: str | Sequence[str] | None = "20260715_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("core_assistant_turns", sa.Column("model_selection", sa.JSON()))
    op.add_column("core_assistant_turns", sa.Column("routing_decision", sa.JSON()))
    op.add_column(
        "core_assistant_turns",
        sa.Column("budget_approval_run_id", sa.String(36)),
    )
    op.add_column(
        "core_assistant_provider_attempts",
        sa.Column("model_id", sa.String(255), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "core_assistant_provider_attempts",
        sa.Column("endpoint_kind", sa.String(32), nullable=False, server_default="chat"),
    )
    op.add_column(
        "core_assistant_provider_attempts",
        sa.Column("model_role", sa.String(32), nullable=False, server_default="primary"),
    )
    op.add_column(
        "core_assistant_provider_attempts",
        sa.Column("usage_cost", sa.String(64)),
    )
    op.create_check_constraint(
        "ck_core_provider_attempts_endpoint_kind",
        "core_assistant_provider_attempts",
        "endpoint_kind IN ('chat', 'images', 'audio', 'videos')",
    )
    op.create_check_constraint(
        "ck_core_provider_attempts_model_role",
        "core_assistant_provider_attempts",
        "model_role IN ('coordinator', 'primary', 'reviewer')",
    )
    op.alter_column(
        "core_assistant_provider_attempts",
        "model_id",
        server_default=None,
    )
    op.alter_column(
        "core_assistant_provider_attempts",
        "endpoint_kind",
        server_default=None,
    )
    op.alter_column(
        "core_assistant_provider_attempts",
        "model_role",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_core_provider_attempts_model_role",
        "core_assistant_provider_attempts",
        type_="check",
    )
    op.drop_constraint(
        "ck_core_provider_attempts_endpoint_kind",
        "core_assistant_provider_attempts",
        type_="check",
    )
    op.drop_column("core_assistant_provider_attempts", "usage_cost")
    op.drop_column("core_assistant_provider_attempts", "model_role")
    op.drop_column("core_assistant_provider_attempts", "endpoint_kind")
    op.drop_column("core_assistant_provider_attempts", "model_id")
    op.drop_column("core_assistant_turns", "budget_approval_run_id")
    op.drop_column("core_assistant_turns", "routing_decision")
    op.drop_column("core_assistant_turns", "model_selection")
