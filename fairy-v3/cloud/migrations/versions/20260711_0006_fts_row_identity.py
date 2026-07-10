"""Add a stable row identity for SQLite external-content FTS parity."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_0006"
down_revision: str | Sequence[str] | None = "20260711_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memory_search_documents",
        sa.Column("fts_rowid", sa.BigInteger()),
    )
    op.create_check_constraint(
        "ck_memory_search_documents_fts_rowid",
        "memory_search_documents",
        "fts_rowid IS NULL OR fts_rowid > 0",
    )
    op.create_index(
        "uq_memory_search_documents_fts_rowid",
        "memory_search_documents",
        ["fts_rowid"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_memory_search_documents_fts_rowid",
        table_name="memory_search_documents",
    )
    op.drop_constraint(
        "ck_memory_search_documents_fts_rowid",
        "memory_search_documents",
        type_="check",
    )
    op.drop_column("memory_search_documents", "fts_rowid")
