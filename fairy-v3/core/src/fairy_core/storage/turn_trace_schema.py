from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
)

from fairy_core.persistence.tenant import TENANT_ID_LENGTH
from fairy_core.storage.types import UTCDateTime

ID_LENGTH = 36


def build_turn_trace_tables(
    metadata: MetaData,
    *,
    assistant_turns: Table,
    provider_attempts: Table,
) -> tuple[Table, Table]:
    traces = Table(
        "core_turn_traces",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("id", String(ID_LENGTH), primary_key=True),
        Column("turn_id", String(ID_LENGTH), nullable=False),
        Column("conversation_id", String(ID_LENGTH), nullable=False),
        Column("task_id", String(ID_LENGTH), nullable=False),
        Column("legacy", Boolean, nullable=False),
        Column("last_sequence", BigInteger, nullable=False),
        Column("revision", BigInteger, nullable=False),
        Column("created_at", UTCDateTime(), nullable=False),
        Column("updated_at", UTCDateTime(), nullable=False),
        Column("started_at", UTCDateTime()),
        Column("completed_at", UTCDateTime()),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_turn_traces"),
        UniqueConstraint(
            "tenant_id",
            "turn_id",
            name="uq_core_turn_traces_turn",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            "turn_id",
            name="uq_core_turn_traces_scope",
        ),
        CheckConstraint("last_sequence >= 0", name="ck_core_turn_traces_sequence"),
        CheckConstraint("revision >= 0", name="ck_core_turn_traces_revision"),
        ForeignKeyConstraint(
            ["tenant_id", "turn_id", "conversation_id", "task_id"],
            [
                assistant_turns.c.tenant_id,
                assistant_turns.c.id,
                assistant_turns.c.conversation_id,
                assistant_turns.c.task_id,
            ],
            name="fk_core_turn_traces_turn_scope",
            ondelete="CASCADE",
        ),
    )
    steps = Table(
        "core_turn_trace_steps",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("id", String(ID_LENGTH), primary_key=True),
        Column("trace_id", String(ID_LENGTH), nullable=False),
        Column("turn_id", String(ID_LENGTH), nullable=False),
        Column("sequence", BigInteger, nullable=False),
        Column("parent_step_id", String(ID_LENGTH)),
        Column("caused_by_step_id", String(ID_LENGTH)),
        Column("kind", String(32), nullable=False),
        Column("status", String(32), nullable=False),
        Column("public_summary", String(512), nullable=False),
        Column("public_detail", String(4000)),
        Column("model_id", String(255)),
        Column("model_role", String(32)),
        Column("provider_attempt_id", String(ID_LENGTH)),
        Column("command_run_id", String(ID_LENGTH)),
        Column("artifact_refs", JSON, nullable=False),
        Column("visibility", String(32), nullable=False),
        Column("revision", BigInteger, nullable=False),
        Column("created_at", UTCDateTime(), nullable=False),
        Column("updated_at", UTCDateTime(), nullable=False),
        Column("started_at", UTCDateTime()),
        Column("completed_at", UTCDateTime()),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_turn_trace_steps"),
        UniqueConstraint(
            "tenant_id",
            "trace_id",
            "sequence",
            name="uq_core_turn_trace_steps_sequence",
        ),
        UniqueConstraint(
            "tenant_id",
            "trace_id",
            "id",
            name="uq_core_turn_trace_steps_scope",
        ),
        CheckConstraint("sequence > 0", name="ck_core_turn_trace_steps_sequence"),
        CheckConstraint("revision >= 0", name="ck_core_turn_trace_steps_revision"),
        CheckConstraint(
            "kind IN ('route', 'plan', 'reasoning', 'model', 'tool', 'approval', "
            "'observation', 'verification', 'artifact', 'response', 'voice')",
            name="ck_core_turn_trace_steps_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'waiting', 'succeeded', 'failed', "
            "'cancelled', 'skipped')",
            name="ck_core_turn_trace_steps_status",
        ),
        CheckConstraint(
            "visibility IN ('user', 'developer', 'internal')",
            name="ck_core_turn_trace_steps_visibility",
        ),
        CheckConstraint(
            "model_role IS NULL OR model_role IN ('coordinator', 'primary', 'reviewer')",
            name="ck_core_turn_trace_steps_model_role",
        ),
        CheckConstraint(
            "(model_id IS NULL) = (model_role IS NULL)",
            name="ck_core_turn_trace_steps_model_binding",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "trace_id", "turn_id"],
            [traces.c.tenant_id, traces.c.id, traces.c.turn_id],
            name="fk_core_turn_trace_steps_trace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "provider_attempt_id"],
            [provider_attempts.c.tenant_id, provider_attempts.c.id],
            name="fk_core_turn_trace_steps_provider_attempt",
        ),
    )
    Index(
        "ix_core_turn_trace_steps_turn",
        steps.c.tenant_id,
        steps.c.turn_id,
        steps.c.sequence,
    )
    Index(
        "ix_core_turn_trace_steps_command",
        steps.c.tenant_id,
        steps.c.command_run_id,
    )
    return traces, steps


__all__ = ["build_turn_trace_tables"]
