"""Persist immutable local message submission receipts.

Revision ID: 20260909_0055
Revises: 20260908_0054
"""
import sqlalchemy as sa
from alembic import op

revision = "20260909_0055"
down_revision = "20260908_0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "core_assistant_message_submissions",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("key_digest", sa.String(64), primary_key=True),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(key_digest) = 64 AND length(request_digest) = 64",
                           name="ck_core_message_submission_hashes"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_message_submission_conversation", ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("core_assistant_message_submissions")
