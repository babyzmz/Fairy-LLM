"""Bind new interpretations to immutable execution intent snapshots.

Revision ID: 20260908_0054
Revises: 20260809_0053
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_0054"
down_revision: str | Sequence[str] | None = "20260809_0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "core_assistant_request_interpretations"


def upgrade() -> None:
    # Existing rows deliberately remain NULL, not implicitly authorized.
    op.add_column(TABLE, sa.Column("execution_intent", sa.JSON(none_as_null=True)))


def downgrade() -> None:
    op.drop_column(TABLE, "execution_intent")
