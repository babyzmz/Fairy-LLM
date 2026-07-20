"""Promote Workspace to a first-class aggregate for projects and scratch chats."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0020"
down_revision: str | Sequence[str] | None = "20260713_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_TABLE = "core_workspaces"
WORKSPACE_OWNERS = (
    "core_projects",
    "core_conversations",
    "core_versions",
    "core_tasks",
    "core_task_workspaces",
    "core_project_indexes",
    "core_changesets",
)


def upgrade() -> None:
    op.create_table(
        WORKSPACE_TABLE,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("active_version_id", sa.String(36)),
        sa.Column("active_preview_id", sa.String(36)),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("max_files", sa.BigInteger(), nullable=False),
        sa.Column("max_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_workspaces"),
        sa.CheckConstraint("revision >= 0", name="ck_core_workspaces_revision"),
        sa.CheckConstraint("max_files > 0", name="ck_core_workspaces_max_files"),
        sa.CheckConstraint("max_bytes > 0", name="ck_core_workspaces_max_bytes"),
    )
    _enable_rls(WORKSPACE_TABLE)

    for table_name in WORKSPACE_OWNERS:
        op.add_column(table_name, sa.Column("workspace_id", sa.String(36)))

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "INSERT INTO core_workspaces "
            "(tenant_id, id, active_version_id, active_preview_id, revision, max_files, "
            "max_bytes, created_at, updated_at) "
            "SELECT tenant_id, id, active_version_id, active_preview_id, revision, 200, "
            "20971520, created_at, updated_at FROM core_projects"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO core_workspaces "
            "(tenant_id, id, active_version_id, active_preview_id, revision, max_files, "
            "max_bytes, created_at, updated_at) "
            "SELECT c.tenant_id, c.id, COALESCE(c.active_draft_version_id, c.base_version_id), "
            "c.active_preview_id, c.revision, 200, 20971520, c.created_at, c.updated_at "
            "FROM core_conversations c WHERE c.project_id IS NULL"
        )
    )
    connection.execute(sa.text("UPDATE core_projects SET workspace_id = id"))
    connection.execute(
        sa.text(
            "UPDATE core_conversations c SET workspace_id = COALESCE(p.workspace_id, c.id) "
            "FROM core_projects p WHERE p.tenant_id = c.tenant_id AND p.id = c.project_id"
        )
    )
    connection.execute(
        sa.text("UPDATE core_conversations SET workspace_id = id WHERE project_id IS NULL")
    )
    connection.execute(
        sa.text(
            "UPDATE core_versions item SET workspace_id = p.workspace_id "
            "FROM core_projects p WHERE p.tenant_id = item.tenant_id "
            "AND p.id = item.project_id"
        )
    )
    for table_name in ("core_versions", "core_tasks", "core_changesets"):
        connection.execute(
            sa.text(
                f"UPDATE {table_name} item SET workspace_id = c.workspace_id "
                "FROM core_conversations c WHERE c.tenant_id = item.tenant_id "
                "AND item.workspace_id IS NULL "
                "AND c.id = item."
                + ("source_conversation_id" if table_name == "core_versions" else "conversation_id")
            )
        )
    connection.execute(
        sa.text(
            "UPDATE core_task_workspaces item SET workspace_id = c.workspace_id "
            "FROM core_conversations c WHERE c.tenant_id = item.tenant_id "
            "AND c.id = item.conversation_id"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE core_project_indexes item SET workspace_id = v.workspace_id "
            "FROM core_versions v WHERE v.tenant_id = item.tenant_id "
            "AND v.id = item.version_id"
        )
    )

    op.drop_constraint(
        "ck_core_task_workspaces_project_version",
        "core_task_workspaces",
        type_="check",
    )
    for table_name in ("core_versions", "core_project_indexes", "core_changesets"):
        op.alter_column(table_name, "project_id", existing_type=sa.String(36), nullable=True)
    for table_name in WORKSPACE_OWNERS:
        ondelete = None if table_name in {"core_projects", "core_conversations"} else "CASCADE"
        op.alter_column(
            table_name,
            "workspace_id",
            existing_type=sa.String(36),
            nullable=False,
        )
        op.create_foreign_key(
            f"fk_{table_name}_workspace",
            table_name,
            WORKSPACE_TABLE,
            ["tenant_id", "workspace_id"],
            ["tenant_id", "id"],
            ondelete=ondelete,
        )
        op.create_index(
            f"ix_{table_name}_tenant_workspace",
            table_name,
            ["tenant_id", "workspace_id"],
        )


def downgrade() -> None:
    for table_name in reversed(WORKSPACE_OWNERS):
        op.drop_index(
            f"ix_{table_name}_tenant_workspace",
            table_name=table_name,
        )
        op.drop_constraint(
            f"fk_{table_name}_workspace",
            table_name,
            type_="foreignkey",
        )
    op.create_check_constraint(
        "ck_core_task_workspaces_project_version",
        "core_task_workspaces",
        "(project_id IS NULL AND version_id IS NULL) OR "
        "(project_id IS NOT NULL AND version_id IS NOT NULL)",
    )
    for table_name in ("core_versions", "core_project_indexes", "core_changesets"):
        op.alter_column(table_name, "project_id", existing_type=sa.String(36), nullable=False)
    for table_name in reversed(WORKSPACE_OWNERS):
        op.drop_column(table_name, "workspace_id")
    policy_name = f"tenant_isolation_{WORKSPACE_TABLE}"
    op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{WORKSPACE_TABLE}"'))
    op.execute(sa.text(f'ALTER TABLE "{WORKSPACE_TABLE}" DISABLE ROW LEVEL SECURITY'))
    op.drop_table(WORKSPACE_TABLE)


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
