"""Persist tenant-scoped Assistant Messages, Turns, and Tool Invocations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_0008"
down_revision: str | Sequence[str] | None = "20260711_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_LENGTH = 128
ID_LENGTH = 36
_RLS_TABLES = (
    "core_assistant_turns",
    "core_assistant_message_sequences",
    "core_assistant_messages",
    "core_assistant_tool_invocations",
)


def upgrade() -> None:
    _create_turns()
    _create_message_sequences()
    _create_messages()
    _create_tool_invocations()
    _enable_rls()


def _create_turns() -> None:
    op.create_table(
        "core_assistant_turns",
        _tenant_column(),
        _id_column(),
        sa.Column("conversation_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("task_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("profile_id", sa.String(255), nullable=False),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("memory_snapshot_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("memory_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("cancellation_revision", sa.BigInteger(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(128)),
        _created_at_column(),
        _updated_at_column(),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_assistant_turns"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_core_assistant_turns_tenant_idempotency",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            "conversation_id",
            "task_id",
            name="uq_core_assistant_turns_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            "task_id",
            name="uq_core_assistant_turns_task_scope",
        ),
        sa.CheckConstraint(
            "status IN ('created', 'running', 'waiting_for_tool', 'completed', "
            "'cancelled', 'failed')",
            name="ck_core_assistant_turns_status",
        ),
        sa.CheckConstraint(
            "cancellation_revision >= 0",
            name="ck_core_assistant_turns_cancellation_revision",
        ),
        sa.CheckConstraint(
            "length(scope_digest) = 64 AND scope_digest = lower(scope_digest)",
            name="ck_core_assistant_turns_scope_digest",
        ),
        sa.CheckConstraint(
            "length(memory_snapshot_hash) = 64 AND "
            "memory_snapshot_hash = lower(memory_snapshot_hash)",
            name="ck_core_assistant_turns_memory_snapshot_hash",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_assistant_turns_conversation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_core_assistant_turns_task",
        ),
    )
    op.create_index(
        "ix_core_assistant_turns_tenant_task",
        "core_assistant_turns",
        ["tenant_id", "task_id", "created_at"],
    )


def _create_messages() -> None:
    op.create_table(
        "core_assistant_messages",
        _tenant_column(),
        _id_column(),
        sa.Column("conversation_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("task_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("turn_id", sa.String(ID_LENGTH)),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("visibility", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        _created_at_column(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_assistant_messages"),
        sa.UniqueConstraint(
            "tenant_id",
            "conversation_id",
            "sequence",
            name="uq_core_assistant_messages_conversation_sequence",
        ),
        sa.CheckConstraint("sequence > 0", name="ck_core_assistant_messages_sequence"),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'tool', 'system_notice')",
            name="ck_core_assistant_messages_role",
        ),
        sa.CheckConstraint(
            "visibility IN ('user', 'developer', 'internal')",
            name="ck_core_assistant_messages_visibility",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_assistant_messages_conversation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_core_assistant_messages_task",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "turn_id", "conversation_id", "task_id"],
            [
                "core_assistant_turns.tenant_id",
                "core_assistant_turns.id",
                "core_assistant_turns.conversation_id",
                "core_assistant_turns.task_id",
            ],
            name="fk_core_assistant_messages_turn_scope",
        ),
    )
    op.create_index(
        "ix_core_assistant_messages_tenant_conversation",
        "core_assistant_messages",
        ["tenant_id", "conversation_id", "sequence"],
    )


def _create_message_sequences() -> None:
    op.create_table(
        "core_assistant_message_sequences",
        _tenant_column(),
        sa.Column("conversation_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "conversation_id",
            name="pk_core_assistant_message_sequences",
        ),
        sa.CheckConstraint(
            "last_sequence > 0",
            name="ck_core_assistant_message_sequences_positive",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_assistant_message_sequences_conversation",
        ),
    )


def _create_tool_invocations() -> None:
    op.create_table(
        "core_assistant_tool_invocations",
        _tenant_column(),
        _id_column(),
        sa.Column("turn_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("task_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("argument_hash", sa.String(64), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("command_run_id", sa.String(ID_LENGTH)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("public_summary", sa.Text()),
        sa.Column("artifact_ids", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(128)),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "id",
            name="pk_core_assistant_tool_invocations",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "turn_id",
            "sequence",
            name="uq_core_assistant_tool_invocations_turn_sequence",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "turn_id",
            "argument_hash",
            name="uq_core_assistant_tool_invocations_turn_arguments",
        ),
        sa.CheckConstraint(
            "sequence > 0",
            name="ck_core_assistant_tool_invocations_sequence",
        ),
        sa.CheckConstraint(
            "status IN ('created', 'queued', 'running', 'completed', 'failed', "
            "'rejected', 'cancelled')",
            name="ck_core_assistant_tool_invocations_status",
        ),
        sa.CheckConstraint(
            "length(scope_digest) = 64 AND scope_digest = lower(scope_digest)",
            name="ck_core_assistant_tool_invocations_scope_digest",
        ),
        sa.CheckConstraint(
            "length(argument_hash) = 64 AND argument_hash = lower(argument_hash)",
            name="ck_core_assistant_tool_invocations_argument_hash",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "turn_id", "task_id"],
            [
                "core_assistant_turns.tenant_id",
                "core_assistant_turns.id",
                "core_assistant_turns.task_id",
            ],
            name="fk_core_assistant_tool_invocations_turn_task",
        ),
    )
    op.create_index(
        "ix_core_assistant_tool_invocations_tenant_turn",
        "core_assistant_tool_invocations",
        ["tenant_id", "turn_id", "sequence"],
    )


def _enable_rls() -> None:
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
    for table_name in _RLS_TABLES:
        policy_name = f"tenant_isolation_{table_name}"
        op.execute(sa.text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
        op.execute(
            sa.text(
                f'CREATE POLICY "{policy_name}" ON "{table_name}" '
                f"USING ({predicate}) WITH CHECK ({predicate})"
            )
        )


def downgrade() -> None:
    for table_name in reversed(_RLS_TABLES):
        policy_name = f"tenant_isolation_{table_name}"
        op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"'))
        op.execute(sa.text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))
    op.drop_index(
        "ix_core_assistant_tool_invocations_tenant_turn",
        table_name="core_assistant_tool_invocations",
    )
    op.drop_table("core_assistant_tool_invocations")
    op.drop_index(
        "ix_core_assistant_messages_tenant_conversation",
        table_name="core_assistant_messages",
    )
    op.drop_table("core_assistant_messages")
    op.drop_table("core_assistant_message_sequences")
    op.drop_index(
        "ix_core_assistant_turns_tenant_task",
        table_name="core_assistant_turns",
    )
    op.drop_table("core_assistant_turns")


def _tenant_column() -> sa.Column[str]:
    return sa.Column("tenant_id", sa.String(TENANT_LENGTH), nullable=False)


def _id_column() -> sa.Column[str]:
    return sa.Column("id", sa.String(ID_LENGTH), nullable=False)


def _created_at_column() -> sa.Column[datetime]:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)


def _updated_at_column() -> sa.Column[datetime]:
    return sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)
