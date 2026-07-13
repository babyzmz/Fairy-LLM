"""Persist Runtime service graphs on cloud leases.

Revision ID: 20260713_0023
Revises: 20260713_0022
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0023"
down_revision: str | None = "20260713_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "core_runtime_sessions",
        sa.Column("runtime_graph", sa.JSON(), nullable=True),
    )
    op.execute(
        "UPDATE core_runtime_sessions SET runtime_graph = json_build_object("
        "'public_service_id', 'app', 'services', json_build_array(json_build_object("
        "'service_id', 'app', 'adapter', CASE WHEN kind = 'static_site' THEN "
        "'static' ELSE 'legacy' END, 'cwd', '.', 'readiness_path', '/', "
        "'depends_on', json_build_array())))"
    )
    op.alter_column("core_runtime_sessions", "runtime_graph", nullable=False)
    op.add_column(
        "runtime_leases",
        sa.Column("services", sa.JSON(), nullable=True),
    )
    op.add_column(
        "runtime_leases",
        sa.Column("public_service_id", sa.String(length=32), nullable=True),
    )
    op.execute(
        "UPDATE runtime_leases SET services = json_build_array(json_build_object("
        "'service_id', 'app', 'adapter', adapter, 'argv', argv, 'cwd', cwd, "
        "'readiness_path', readiness_path, 'startup_timeout_seconds', "
        "startup_timeout_seconds, 'depends_on', json_build_array())), "
        "public_service_id = 'app'"
    )
    op.alter_column("runtime_leases", "services", nullable=False)
    op.alter_column("runtime_leases", "public_service_id", nullable=False)


def downgrade() -> None:
    op.drop_column("runtime_leases", "public_service_id")
    op.drop_column("runtime_leases", "services")
    op.drop_column("core_runtime_sessions", "runtime_graph")
