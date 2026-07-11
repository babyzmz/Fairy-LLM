"""Generalize approvals for resumable Assistant Tool Invocations."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_0012"
down_revision: str | Sequence[str] | None = "20260711_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INVOCATIONS = "core_assistant_tool_invocations"
APPROVALS = "core_approvals"


def upgrade() -> None:
    op.add_column(INVOCATIONS, sa.Column("model_round", sa.BigInteger(), nullable=True))
    op.add_column(
        INVOCATIONS,
        sa.Column("provider_call_id", sa.String(255), nullable=True),
    )
    op.add_column(INVOCATIONS, sa.Column("model_content", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE core_assistant_tool_invocations "
            "SET model_round = 1, provider_call_id = 'legacy-' || id, "
            "model_content = public_summary"
        )
    )
    op.alter_column(INVOCATIONS, "model_round", nullable=False)
    op.alter_column(INVOCATIONS, "provider_call_id", nullable=False)
    op.create_check_constraint(
        "ck_core_assistant_tool_invocations_model_round",
        INVOCATIONS,
        "model_round > 0",
    )
    op.create_unique_constraint(
        "uq_core_assistant_tool_invocations_turn_provider_call",
        INVOCATIONS,
        ["tenant_id", "turn_id", "provider_call_id"],
    )

    op.add_column(
        APPROVALS,
        sa.Column("tool_invocation_id", sa.String(36), nullable=True),
    )
    op.create_unique_constraint(
        "uq_core_approvals_tenant_command_run",
        APPROVALS,
        ["tenant_id", "command_run_id"],
    )
    op.create_unique_constraint(
        "uq_core_approvals_tenant_tool_invocation",
        APPROVALS,
        ["tenant_id", "tool_invocation_id"],
    )
    op.create_check_constraint(
        "ck_core_approvals_single_subject",
        APPROVALS,
        "changeset_id IS NULL OR tool_invocation_id IS NULL",
    )
    op.create_foreign_key(
        "fk_core_approvals_tool_invocation",
        APPROVALS,
        INVOCATIONS,
        ["tenant_id", "tool_invocation_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_core_approvals_tool_invocation",
        APPROVALS,
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_core_approvals_single_subject",
        APPROVALS,
        type_="check",
    )
    op.drop_constraint(
        "uq_core_approvals_tenant_tool_invocation",
        APPROVALS,
        type_="unique",
    )
    op.drop_constraint(
        "uq_core_approvals_tenant_command_run",
        APPROVALS,
        type_="unique",
    )
    op.drop_column(APPROVALS, "tool_invocation_id")

    op.drop_constraint(
        "uq_core_assistant_tool_invocations_turn_provider_call",
        INVOCATIONS,
        type_="unique",
    )
    op.drop_constraint(
        "ck_core_assistant_tool_invocations_model_round",
        INVOCATIONS,
        type_="check",
    )
    op.drop_column(INVOCATIONS, "model_content")
    op.drop_column(INVOCATIONS, "provider_call_id")
    op.drop_column(INVOCATIONS, "model_round")
