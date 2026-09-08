"""Persist cancellation intent before a message submission has a Turn.

Revision ID: 20260909_0056
Revises: 20260909_0055
"""
import sqlalchemy as sa
from alembic import op

revision = "20260909_0056"
down_revision = "20260909_0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "core_assistant_message_cancellations",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("key_digest", sa.String(64), primary_key=True),
        sa.Column("conversation_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(key_digest) = 64", name="ck_core_message_cancellation_hash"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_message_cancellation_conversation", ondelete="CASCADE",
        ),
    )
    table = "core_assistant_message_cancellations"
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
    op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
    op.execute(sa.text(
        f'CREATE POLICY "tenant_isolation_{table}" ON "{table}" '
        f"USING ({predicate}) WITH CHECK ({predicate})"
    ))


def downgrade() -> None:
    op.drop_table("core_assistant_message_cancellations")
