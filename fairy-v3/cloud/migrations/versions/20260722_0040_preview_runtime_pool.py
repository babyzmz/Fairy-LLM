"""Add durable Preview Runtime pool access metadata.

Revision ID: 20260722_0040
Revises: 20260721_0039
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_0040"
down_revision: str | Sequence[str] | None = "20260721_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "core_preview_sessions"
INDEX = "ix_core_preview_sessions_tenant_active_access"


def upgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(sa.Column("last_accessed_at", sa.DateTime(timezone=True)))
    op.execute(
        sa.text(
            "UPDATE core_preview_sessions "
            "SET last_accessed_at = updated_at "
            "WHERE last_accessed_at IS NULL"
        )
    )
    with op.batch_alter_table(TABLE) as batch:
        batch.alter_column(
            "last_accessed_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
        batch.create_index(
            INDEX,
            ["tenant_id", "status", "last_accessed_at", "id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_index(INDEX)
        batch.drop_column("last_accessed_at")
