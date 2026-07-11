"""Persist Task Workspaces and deterministic Project Index generations."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_0013"
down_revision: str | Sequence[str] | None = "20260711_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACES = "core_task_workspaces"
INDEXES = "core_project_indexes"
TABLES = (WORKSPACES, INDEXES)


def upgrade() -> None:
    op.create_table(
        WORKSPACES,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=True),
        sa.Column("root", sa.String(4096), nullable=False),
        sa.Column("editable_files", sa.JSON(), nullable=False),
        sa.Column("reference_files", sa.JSON(), nullable=False),
        sa.Column("constraints", sa.JSON(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "task_id",
            name="pk_core_task_workspaces",
        ),
        sa.CheckConstraint(
            "generation > 0",
            name="ck_core_task_workspaces_generation",
        ),
        sa.CheckConstraint(
            "(project_id IS NULL AND version_id IS NULL) OR "
            "(project_id IS NOT NULL AND version_id IS NOT NULL)",
            name="ck_core_task_workspaces_project_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_core_task_workspaces_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_core_task_workspaces_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_task_workspaces_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_core_task_workspaces_version",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_task_workspaces_tenant_version",
        WORKSPACES,
        ["tenant_id", "version_id"],
    )
    op.create_table(
        INDEXES,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "version_id",
            name="pk_core_project_indexes",
        ),
        sa.CheckConstraint(
            "generation > 0",
            name="ck_core_project_indexes_generation",
        ),
        sa.CheckConstraint(
            "length(source_hash) = 64 AND source_hash = lower(source_hash)",
            name="ck_core_project_indexes_source_hash",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_core_project_indexes_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_core_project_indexes_project",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_project_indexes_tenant_project",
        INDEXES,
        ["tenant_id", "project_id"],
    )
    for table_name in TABLES:
        _enable_rls(table_name)


def downgrade() -> None:
    for table_name in reversed(TABLES):
        policy_name = f"tenant_isolation_{table_name}"
        op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"'))
        op.execute(sa.text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))
        op.drop_table(table_name)


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
