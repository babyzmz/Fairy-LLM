"""Constrain pending Assistant schedule work.

Revision ID: 20260807_0049
Revises: 20260807_0048
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0049"
down_revision: str | Sequence[str] | None = "20260807_0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OCCURRENCES = "core_assistant_schedule_occurrences"
INDEX = "uq_core_assistant_occurrences_pending"


def upgrade() -> None:
    predicate = sa.text("status = 'pending'")
    op.create_index(
        INDEX,
        OCCURRENCES,
        ["tenant_id", "schedule_id"],
        unique=True,
        postgresql_where=predicate,
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name=OCCURRENCES)
