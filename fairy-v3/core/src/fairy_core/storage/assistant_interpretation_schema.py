from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
)

from fairy_core.storage.types import UTCDateTime


def build_assistant_interpretation_table(
    metadata: MetaData,
    *,
    assistant_turns: Table,
    assistant_messages: Table,
    tenant_id_length: int,
    id_length: int,
) -> Table:
    return Table(
        "core_assistant_request_interpretations",
        metadata,
        Column("tenant_id", String(tenant_id_length), primary_key=True),
        Column("id", String(id_length), primary_key=True),
        Column("turn_id", String(id_length), nullable=False),
        Column("revision", BigInteger, nullable=False),
        Column("idempotency_key", String(512), nullable=False),
        Column("source_message_id", String(id_length), nullable=False),
        Column("source_message_sha256", String(64), nullable=False),
        Column("schema_version", Integer, nullable=False),
        Column("normalized_goal", String(4_000), nullable=False),
        Column("action", String(32), nullable=False),
        Column("objectives", JSON, nullable=False),
        Column("targets", JSON, nullable=False),
        Column("constraints", JSON, nullable=False),
        Column("deliverable", String(2_000)),
        Column("evidence_requirements", JSON, nullable=False),
        Column("assumptions", JSON, nullable=False),
        Column("missing_information", JSON, nullable=False),
        Column("confidence", String(16), nullable=False),
        Column("disposition", String(32), nullable=False),
        Column("public_summary", String(240), nullable=False),
        Column("clarification_question", String(1_000)),
        Column("created_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint(
            "tenant_id",
            "id",
            name="pk_core_assistant_request_interpretations",
        ),
        UniqueConstraint(
            "tenant_id",
            "turn_id",
            "revision",
            name="uq_core_assistant_interpretations_revision",
        ),
        UniqueConstraint(
            "tenant_id",
            "turn_id",
            "idempotency_key",
            name="uq_core_assistant_interpretations_idempotency",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_core_assistant_interpretations_revision",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_core_assistant_interpretations_schema",
        ),
        CheckConstraint(
            "length(source_message_sha256) = 64 "
            "AND source_message_sha256 = lower(source_message_sha256)",
            name="ck_core_assistant_interpretations_source_hash",
        ),
        CheckConstraint(
            "action IN ('answer','explain','review','change','create','run','browse',"
            "'generate','schedule','manage')",
            name="ck_core_assistant_interpretations_action",
        ),
        CheckConstraint(
            "confidence IN ('low','medium','high')",
            name="ck_core_assistant_interpretations_confidence",
        ),
        CheckConstraint(
            "disposition IN ('ready','assumed','clarification_required')",
            name="ck_core_assistant_interpretations_disposition",
        ),
        CheckConstraint(
            "(disposition = 'clarification_required' "
            "AND clarification_question IS NOT NULL) OR "
            "(disposition != 'clarification_required' "
            "AND clarification_question IS NULL)",
            name="ck_core_assistant_interpretations_clarification",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "turn_id"],
            [assistant_turns.c.tenant_id, assistant_turns.c.id],
            name="fk_core_assistant_interpretations_turn",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_message_id"],
            [assistant_messages.c.tenant_id, assistant_messages.c.id],
            name="fk_core_assistant_interpretations_source_message",
            ondelete="CASCADE",
        ),
    )


__all__ = ["build_assistant_interpretation_table"]
