from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
)

from fairy_core.persistence.tenant import TENANT_ID_LENGTH
from fairy_core.storage.types import UTCDateTime

ID_LENGTH = 36


def build_assistant_attempt_tables(
    metadata: MetaData,
    *,
    assistant_turns: Table,
    conversations: Table,
) -> tuple[Table, Table]:
    provider_attempts = Table(
        "core_assistant_provider_attempts",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("id", String(ID_LENGTH), primary_key=True),
        Column("turn_id", String(ID_LENGTH), nullable=False),
        Column("task_id", String(ID_LENGTH), nullable=False),
        Column("model_round", BigInteger, nullable=False),
        Column("attempt_number", BigInteger, nullable=False),
        Column("profile_id", String(255), nullable=False),
        Column("model_id", String(255), nullable=False),
        Column("endpoint_kind", String(32), nullable=False),
        Column("model_role", String(32), nullable=False),
        Column("status", String(32), nullable=False),
        Column("error_category", String(32)),
        Column("usage", JSON, nullable=False),
        Column("usage_cost", String(64)),
        Column("created_at", UTCDateTime(), nullable=False),
        Column("completed_at", UTCDateTime()),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_assistant_provider_attempts"),
        UniqueConstraint(
            "tenant_id",
            "turn_id",
            "model_round",
            "attempt_number",
            name="uq_core_assistant_provider_attempts_turn_round_number",
        ),
        CheckConstraint("model_round > 0", name="ck_core_provider_attempts_model_round"),
        CheckConstraint("attempt_number > 0", name="ck_core_provider_attempts_number"),
        CheckConstraint(
            "status IN ('started', 'succeeded', 'failed')",
            name="ck_core_provider_attempts_status",
        ),
        CheckConstraint(
            "endpoint_kind IN ('chat', 'images', 'audio', 'videos')",
            name="ck_core_provider_attempts_endpoint_kind",
        ),
        CheckConstraint(
            "model_role IN ('coordinator', 'primary', 'reviewer')",
            name="ck_core_provider_attempts_model_role",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "turn_id", "task_id"],
            [assistant_turns.c.tenant_id, assistant_turns.c.id, assistant_turns.c.task_id],
            name="fk_core_provider_attempts_turn_task",
        ),
    )
    message_sequences = Table(
        "core_assistant_message_sequences",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("conversation_id", String(ID_LENGTH), primary_key=True),
        Column("last_sequence", BigInteger, nullable=False),
        PrimaryKeyConstraint(
            "tenant_id",
            "conversation_id",
            name="pk_core_assistant_message_sequences",
        ),
        CheckConstraint("last_sequence > 0", name="ck_core_assistant_message_sequences_positive"),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            [conversations.c.tenant_id, conversations.c.id],
            name="fk_core_assistant_message_sequences_conversation",
        ),
    )
    return provider_attempts, message_sequences


__all__ = ["build_assistant_attempt_tables"]
