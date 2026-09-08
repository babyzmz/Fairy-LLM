"""Bind tool identity to explicit Workflow plan revisions.

Revision ID: 20260909_0060
Revises: 20260909_0059
"""

import sqlalchemy as sa
from alembic import op
from fairy_core.storage.tool_revision_migration import tool_revision_backfill_sql

revision = "20260909_0060"
down_revision = "20260909_0059"
branch_labels = None
depends_on = None

TABLE = "core_assistant_tool_invocations"


def upgrade() -> None:
    invalid, backfill = tool_revision_backfill_sql(postgres=True)
    op.execute(
        sa.text(
            f"DO $$ BEGIN IF EXISTS ({invalid}) THEN "
            "RAISE EXCEPTION 'Tool Invocation history has ambiguous Workflow provenance'; "
            "END IF; END $$;"
        )
    )
    op.add_column(TABLE, sa.Column("workflow_run_id", sa.String(36)))
    op.add_column(
        TABLE,
        sa.Column("workflow_plan_revision", sa.BigInteger(), nullable=False, server_default="1"),
    )
    op.drop_constraint("uq_core_assistant_tool_invocations_turn_arguments", TABLE, type_="unique")
    op.drop_constraint("uq_core_assistant_tool_invocations_turn_provider_call", TABLE,
                       type_="unique")
    op.create_unique_constraint(
        "uq_core_assistant_tool_invocations_revision_provider_call", TABLE,
        ["tenant_id", "turn_id", "workflow_plan_revision", "provider_call_id"],
    )
    op.create_unique_constraint(
        "uq_core_assistant_tool_invocations_revision_arguments",
        TABLE,
        ["tenant_id", "turn_id", "workflow_plan_revision", "argument_hash"],
    )
    op.create_check_constraint(
        "ck_core_assistant_tool_invocations_workflow_binding",
        TABLE,
        "workflow_plan_revision > 0 AND "
        "(workflow_run_id IS NOT NULL OR workflow_plan_revision = 1)",
    )
    op.create_foreign_key(
        "fk_core_assistant_tool_invocations_workflow_revision",
        TABLE,
        "core_workflow_plan_revisions",
        ["tenant_id", "workflow_run_id", "workflow_plan_revision"],
        ["tenant_id", "run_id", "revision"],
    )
    op.execute(sa.text(backfill))


def downgrade() -> None:
    # Never erase provenance/history to make the old uniqueness constraint fit.
    op.execute(
        sa.text(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM core_assistant_tool_invocations "
            "WHERE workflow_run_id IS NOT NULL OR workflow_plan_revision <> 1) THEN "
            "RAISE EXCEPTION 'Versioned tool calls require a matching recovery backup'; "
            "END IF; END $$;"
        )
    )
    op.drop_constraint(
        "fk_core_assistant_tool_invocations_workflow_revision", TABLE, type_="foreignkey"
    )
    op.drop_constraint("ck_core_assistant_tool_invocations_workflow_binding", TABLE, type_="check")
    op.drop_constraint(
        "uq_core_assistant_tool_invocations_revision_arguments", TABLE, type_="unique"
    )
    op.create_unique_constraint(
        "uq_core_assistant_tool_invocations_turn_arguments",
        TABLE,
        ["tenant_id", "turn_id", "argument_hash"],
    )
    op.drop_constraint("uq_core_assistant_tool_invocations_revision_provider_call", TABLE,
                       type_="unique")
    op.create_unique_constraint("uq_core_assistant_tool_invocations_turn_provider_call", TABLE,
                                ["tenant_id", "turn_id", "provider_call_id"])
    op.drop_column(TABLE, "workflow_plan_revision")
    op.drop_column(TABLE, "workflow_run_id")
