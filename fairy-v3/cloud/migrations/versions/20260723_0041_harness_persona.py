"""Bind Harness Manifests to the immutable Fairy Persona.

Revision ID: 20260723_0041
Revises: 20260722_0040
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0041"
down_revision: str | Sequence[str] | None = "20260722_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "core_harness_context_manifests"
LEGACY_DIGEST = "0" * 64


def upgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(
            sa.Column("persona_version", sa.String(64), nullable=False, server_default="legacy")
        )
        batch.add_column(
            sa.Column(
                "persona_digest",
                sa.String(64),
                nullable=False,
                server_default=LEGACY_DIGEST,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_column("persona_digest")
        batch.drop_column("persona_version")
