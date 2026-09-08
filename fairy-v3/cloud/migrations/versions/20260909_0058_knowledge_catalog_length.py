"""Persist display metadata without changing immutable Knowledge hashes.

Revision ID: 20260909_0058
Revises: 20260909_0057
"""
import sqlalchemy as sa
from alembic import op

revision = "20260909_0058"
down_revision = "20260909_0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("core_knowledge_revisions", sa.Column("byte_length", sa.BigInteger()))
    # The migration role must already have BYPASSRLS; never weaken tenant policies.
    op.execute(sa.text(
        "UPDATE core_knowledge_revisions "
        "SET byte_length = octet_length(convert_to(content, 'UTF8'))"
    ))
    op.alter_column(
        "core_knowledge_revisions", "byte_length", nullable=False, server_default="0",
    )


def downgrade() -> None:
    op.drop_column("core_knowledge_revisions", "byte_length")
