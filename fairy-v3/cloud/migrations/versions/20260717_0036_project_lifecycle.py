"""Add recoverable Project and Conversation lifecycle metadata.

Revision ID: 20260717_0036
Revises: 20260716_0035
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_0036"
down_revision: str | Sequence[str] | None = "20260716_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("core_projects", sa.Column("pinned_at", sa.DateTime(timezone=True)))
    op.add_column("core_projects", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.add_column("core_projects", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("core_projects", sa.Column("purged_at", sa.DateTime(timezone=True)))
    op.add_column(
        "core_projects",
        sa.Column("metadata_revision", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_core_projects_tenant_lifecycle_updated",
        "core_projects",
        ["tenant_id", "deleted_at", "archived_at", "updated_at"],
    )
    op.add_column(
        "core_conversations",
        sa.Column("deleted_by_project_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "core_conversations",
        sa.Column("purged_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_core_conversations_tenant_deleted_updated",
        "core_conversations",
        ["tenant_id", "deleted_at", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_core_conversations_tenant_deleted_updated",
        table_name="core_conversations",
    )
    op.drop_column("core_conversations", "purged_at")
    op.drop_column("core_conversations", "deleted_by_project_at")
    op.drop_index(
        "ix_core_projects_tenant_lifecycle_updated",
        table_name="core_projects",
    )
    op.drop_column("core_projects", "metadata_revision")
    op.drop_column("core_projects", "purged_at")
    op.drop_column("core_projects", "deleted_at")
    op.drop_column("core_projects", "archived_at")
    op.drop_column("core_projects", "pinned_at")
