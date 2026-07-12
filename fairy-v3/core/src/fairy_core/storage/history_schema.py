from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
)


def build_history_tables(
    *,
    metadata,
    tenant_id_column: Callable[[], Column[str]],
    id_column: Callable[[], Column[str]],
    conversations: Table,
    assistant_messages: Table,
    projects: Table,
    utc_datetime: Any,
    id_length: int,
) -> tuple[Table, Table]:
    imported = Table(
        "core_assistant_imported_messages",
        metadata,
        tenant_id_column(),
        id_column(),
        Column("conversation_id", String(id_length), nullable=False),
        Column("task_id", String(id_length), nullable=False),
        Column("turn_id", String(id_length)),
        Column("sequence", BigInteger, nullable=False),
        Column("role", String(32), nullable=False),
        Column("visibility", String(32), nullable=False),
        Column("content", String, nullable=False),
        Column("created_at", utc_datetime(), nullable=False),
        Column("source_conversation_id", String(id_length), nullable=False),
        Column("source_message_id", String(id_length), nullable=False),
        Column("source_hash", String(64), nullable=False),
        Column("imported_at", utc_datetime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_assistant_imported_messages"),
        UniqueConstraint(
            "tenant_id",
            "conversation_id",
            "sequence",
            name="uq_core_assistant_imported_messages_conversation_sequence",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_message_id",
            name="uq_core_assistant_imported_messages_source",
        ),
        CheckConstraint("sequence > 0", name="ck_core_assistant_imported_messages_sequence"),
        CheckConstraint(
            "length(source_hash) = 64",
            name="ck_core_assistant_imported_messages_source_hash",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            [conversations.c.tenant_id, conversations.c.id],
            name="fk_core_assistant_imported_messages_conversation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_message_id"],
            [assistant_messages.c.tenant_id, assistant_messages.c.id],
            name="fk_core_assistant_imported_messages_source",
        ),
    )
    moves = Table(
        "core_conversation_moves",
        metadata,
        tenant_id_column(),
        Column("idempotency_key", String(512), primary_key=True),
        Column("source_conversation_id", String(id_length), nullable=False),
        Column("destination_conversation_id", String(id_length), nullable=False),
        Column("target_project_id", String(id_length), nullable=False),
        Column("imported_count", BigInteger, nullable=False),
        Column("created_at", utc_datetime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", "idempotency_key", name="pk_core_conversation_moves"),
        UniqueConstraint(
            "tenant_id",
            "source_conversation_id",
            name="uq_core_conversation_moves_source",
        ),
        CheckConstraint("imported_count >= 0", name="ck_core_conversation_moves_count"),
        ForeignKeyConstraint(
            ["tenant_id", "source_conversation_id"],
            [conversations.c.tenant_id, conversations.c.id],
            name="fk_core_conversation_moves_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "destination_conversation_id"],
            [conversations.c.tenant_id, conversations.c.id],
            name="fk_core_conversation_moves_destination",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "target_project_id"],
            [projects.c.tenant_id, projects.c.id],
            name="fk_core_conversation_moves_project",
        ),
    )
    return imported, moves


__all__ = ["build_history_tables"]
