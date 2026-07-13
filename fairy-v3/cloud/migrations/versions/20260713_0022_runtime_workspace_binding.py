"""Bind Runtime and Preview rows to immutable Workspace Versions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0022"
down_revision: str | Sequence[str] | None = "20260713_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("core_runtime_sessions", "core_preview_sessions")


def upgrade() -> None:
    connection = op.get_bind()
    for table_name in TABLES:
        op.add_column(table_name, sa.Column("workspace_id", sa.String(36)))
        connection.execute(
            sa.text(
                f"UPDATE {table_name} item SET workspace_id = task.workspace_id "
                "FROM core_tasks task WHERE task.tenant_id = item.tenant_id "
                "AND task.id = item.task_id"
            )
        )
        op.alter_column(
            table_name,
            "workspace_id",
            existing_type=sa.String(36),
            nullable=False,
        )
        op.alter_column(
            table_name,
            "version_id",
            existing_type=sa.String(36),
            nullable=False,
        )
        op.create_foreign_key(
            f"fk_{table_name}_workspace",
            table_name,
            "core_workspaces",
            ["tenant_id", "workspace_id"],
            ["tenant_id", "id"],
            ondelete="CASCADE",
        )
        op.create_index(
            f"ix_{table_name}_scope",
            table_name,
            ["tenant_id", "task_id", "workspace_id", "version_id"],
        )


def downgrade() -> None:
    for table_name in reversed(TABLES):
        op.drop_index(f"ix_{table_name}_scope", table_name=table_name)
        op.drop_constraint(
            f"fk_{table_name}_workspace",
            table_name,
            type_="foreignkey",
        )
        op.alter_column(
            table_name,
            "version_id",
            existing_type=sa.String(36),
            nullable=True,
        )
        op.drop_column(table_name, "workspace_id")
