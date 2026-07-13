"""Make Workspace the Runtime lease identity.

Revision ID: 20260713_0024
Revises: 20260713_0023
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0024"
down_revision: str | None = "20260713_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runtime_leases", sa.Column("workspace_id", sa.String(36)))
    op.execute("UPDATE runtime_leases SET workspace_id = project_id")
    op.alter_column(
        "runtime_leases",
        "workspace_id",
        existing_type=sa.String(36),
        nullable=False,
    )
    op.alter_column(
        "runtime_leases",
        "project_id",
        existing_type=sa.String(36),
        nullable=True,
    )
    op.drop_constraint("ck_runtime_leases_template", "runtime_leases", type_="check")
    op.create_check_constraint(
        "ck_runtime_leases_template",
        "runtime_leases",
        "adapter IN ('vite','next','astro','python_asgi','node_http') AND "
        "startup_timeout_seconds BETWEEN 1 AND 120",
    )


def downgrade() -> None:
    op.execute("UPDATE runtime_leases SET project_id = workspace_id WHERE project_id IS NULL")
    op.drop_constraint("ck_runtime_leases_template", "runtime_leases", type_="check")
    op.create_check_constraint(
        "ck_runtime_leases_template",
        "runtime_leases",
        "adapter IN ('vite','next','astro','python_asgi') AND cwd = '.' AND "
        "startup_timeout_seconds BETWEEN 1 AND 120",
    )
    op.alter_column(
        "runtime_leases",
        "project_id",
        existing_type=sa.String(36),
        nullable=False,
    )
    op.drop_column("runtime_leases", "workspace_id")
