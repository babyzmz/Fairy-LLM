"""Add durable Execution Plans and ordered Task Steps."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0021"
down_revision: str | Sequence[str] | None = "20260713_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "core_execution_plans",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("max_model_calls", sa.BigInteger(), nullable=False),
        sa.Column("max_tool_calls", sa.BigInteger(), nullable=False),
        sa.Column("max_repairs", sa.BigInteger(), nullable=False),
        sa.Column("max_duration_seconds", sa.BigInteger(), nullable=False),
        sa.Column("model_calls_used", sa.BigInteger(), nullable=False),
        sa.Column("tool_calls_used", sa.BigInteger(), nullable=False),
        sa.Column("repairs_used", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_execution_plans"),
        sa.UniqueConstraint("tenant_id", "task_id", name="uq_core_execution_plans_task"),
        sa.CheckConstraint("revision >= 0", name="ck_core_execution_plans_revision"),
        sa.CheckConstraint(
            "max_model_calls > 0 AND max_tool_calls > 0 AND max_repairs >= 0",
            name="ck_core_execution_plans_budgets",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_core_execution_plans_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["core_workspaces.tenant_id", "core_workspaces.id"],
            name="fk_core_execution_plans_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_core_execution_plans_version",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_execution_plans_workspace",
        "core_execution_plans",
        ["tenant_id", "workspace_id"],
    )
    _enable_rls("core_execution_plans")
    op.create_table(
        "core_task_steps",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("plan_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.BigInteger(), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_task_steps"),
        sa.UniqueConstraint("tenant_id", "plan_id", "sequence", name="uq_core_task_steps_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_core_task_steps_sequence"),
        sa.CheckConstraint("attempts >= 0", name="ck_core_task_steps_attempts"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "plan_id"],
            ["core_execution_plans.tenant_id", "core_execution_plans.id"],
            name="fk_core_task_steps_plan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_core_task_steps_task",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_task_steps_task",
        "core_task_steps",
        ["tenant_id", "task_id", "sequence"],
    )
    _enable_rls("core_task_steps")


def downgrade() -> None:
    _disable_rls("core_task_steps")
    op.drop_index("ix_core_task_steps_task", table_name="core_task_steps")
    op.drop_table("core_task_steps")
    _disable_rls("core_execution_plans")
    op.drop_index("ix_core_execution_plans_workspace", table_name="core_execution_plans")
    op.drop_table("core_execution_plans")


def _enable_rls(table_name: str) -> None:
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
    policy_name = f"tenant_isolation_{table_name}"
    op.execute(sa.text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            f'CREATE POLICY "{policy_name}" ON "{table_name}" '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    )


def _disable_rls(table_name: str) -> None:
    policy_name = f"tenant_isolation_{table_name}"
    op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"'))
    op.execute(sa.text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))
