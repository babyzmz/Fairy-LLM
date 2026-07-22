"""Persist immutable Harness Persona instructions.

Revision ID: 20260723_0042
Revises: 20260723_0041
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0042"
down_revision: str | Sequence[str] | None = "20260723_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "core_harness_context_manifests"


def upgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(
            sa.Column("persona_instruction", sa.Text(), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_column("persona_instruction")
