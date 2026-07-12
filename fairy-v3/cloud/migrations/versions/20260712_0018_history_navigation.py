"""Add revision-fenced Conversation and Task navigation metadata."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0018"
down_revision: str | Sequence[str] | None = "20260712_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "core_conversations",
        sa.Column(
            "title",
            sa.String(200),
            nullable=False,
            server_default="New conversation",
        ),
    )
    op.add_column(
        "core_conversations",
        sa.Column("pinned_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "core_conversations",
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "core_conversations",
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "core_tasks",
        sa.Column("display_title", sa.String(200), nullable=False, server_default="Task"),
    )
    op.execute(
        "UPDATE core_tasks SET display_title = left(user_request, 200) "
        "WHERE display_title = 'Task'"
    )
    op.add_column(
        "core_tasks",
        sa.Column("pinned_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "core_tasks",
        sa.Column("metadata_revision", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_table(
        "core_assistant_imported_messages",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("turn_id", sa.String(36)),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("visibility", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_conversation_id", sa.String(36), nullable=False),
        sa.Column("source_message_id", sa.String(36), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "id",
            name="pk_core_assistant_imported_messages",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "conversation_id",
            "sequence",
            name="uq_core_assistant_imported_messages_conversation_sequence",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_message_id",
            name="uq_core_assistant_imported_messages_source",
        ),
        sa.CheckConstraint(
            "sequence > 0",
            name="ck_core_assistant_imported_messages_sequence",
        ),
        sa.CheckConstraint(
            "source_hash ~ '^[0-9a-f]{64}$'",
            name="ck_core_assistant_imported_messages_source_hash",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_assistant_imported_messages_conversation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_message_id"],
            ["core_assistant_messages.tenant_id", "core_assistant_messages.id"],
            name="fk_core_assistant_imported_messages_source",
        ),
    )
    op.create_table(
        "core_conversation_moves",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("source_conversation_id", sa.String(36), nullable=False),
        sa.Column("destination_conversation_id", sa.String(36), nullable=False),
        sa.Column("target_project_id", sa.String(36), nullable=False),
        sa.Column("imported_count", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "idempotency_key",
            name="pk_core_conversation_moves",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_conversation_id",
            name="uq_core_conversation_moves_source",
        ),
        sa.CheckConstraint("imported_count >= 0", name="ck_core_conversation_moves_count"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_conversation_moves_source",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "destination_conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_conversation_moves_destination",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "target_project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_core_conversation_moves_project",
        ),
    )


def downgrade() -> None:
    op.drop_table("core_conversation_moves")
    op.drop_table("core_assistant_imported_messages")
    op.drop_column("core_tasks", "metadata_revision")
    op.drop_column("core_tasks", "pinned_at")
    op.drop_column("core_tasks", "display_title")
    op.drop_column("core_conversations", "revision")
    op.drop_column("core_conversations", "deleted_at")
    op.drop_column("core_conversations", "pinned_at")
    op.drop_column("core_conversations", "title")
