"""Preserve file plans across Workflow revisions.

Revision ID: 20260909_0059
Revises: 20260909_0058
"""

import sqlalchemy as sa
from alembic import op

revision = "20260909_0059"
down_revision = "20260909_0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "core_execution_plans",
        sa.Column(
            "generation",
            sa.BigInteger(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column("core_execution_plans", sa.Column("workflow_run_id", sa.String(36)))
    op.add_column("core_execution_plans", sa.Column("workflow_plan_revision", sa.BigInteger()))
    op.drop_constraint("uq_core_execution_plans_task", "core_execution_plans", type_="unique")
    op.create_unique_constraint(
        "uq_core_execution_plans_generation",
        "core_execution_plans",
        ["tenant_id", "task_id", "generation"],
    )
    op.create_unique_constraint(
        "uq_core_execution_plans_workflow_revision",
        "core_execution_plans",
        ["tenant_id", "workflow_run_id", "workflow_plan_revision"],
    )
    op.create_check_constraint(
        "ck_core_execution_plans_generation",
        "core_execution_plans",
        "generation > 0",
    )
    op.create_check_constraint(
        "ck_core_execution_plans_workflow_binding",
        "core_execution_plans",
        "(workflow_run_id IS NULL AND workflow_plan_revision IS NULL) OR "
        "(workflow_run_id IS NOT NULL AND workflow_plan_revision IS NOT NULL "
        "AND workflow_plan_revision > 0)",
    )
    op.create_foreign_key(
        "fk_core_execution_plans_workflow_revision",
        "core_execution_plans",
        "core_workflow_plan_revisions",
        ["tenant_id", "workflow_run_id", "workflow_plan_revision"],
        ["tenant_id", "run_id", "revision"],
    )


def downgrade() -> None:
    # Migration roles need BYPASSRLS to inspect every tenant. Never delete history
    # to satisfy the old uniqueness constraint or silently discard provenance.
    op.execute(
        sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM core_execution_plans
                       WHERE generation <> 1 OR workflow_run_id IS NOT NULL) THEN
                RAISE EXCEPTION 'Versioned file plans require a matching recovery backup';
            END IF;
        END $$;
    """)
    )
    op.drop_constraint(
        "fk_core_execution_plans_workflow_revision",
        "core_execution_plans",
        type_="foreignkey",
    )
    for name in ("generation", "workflow_binding"):
        op.drop_constraint(f"ck_core_execution_plans_{name}", "core_execution_plans", type_="check")
    for name in ("generation", "workflow_revision"):
        op.drop_constraint(
            f"uq_core_execution_plans_{name}", "core_execution_plans", type_="unique"
        )
    op.create_unique_constraint(
        "uq_core_execution_plans_task",
        "core_execution_plans",
        ["tenant_id", "task_id"],
    )
    for name in ("workflow_plan_revision", "workflow_run_id", "generation"):
        op.drop_column("core_execution_plans", name)
