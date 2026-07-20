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


def build_realtime_tables(metadata: MetaData) -> tuple[Table, Table]:
    sessions = Table(
        "core_realtime_sessions",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("id", String(36), primary_key=True),
        Column("idempotency_key", String(512), nullable=False),
        Column("device_id", String(128), nullable=False),
        Column("conversation_id", String(36)),
        Column("provider", String(32), nullable=False),
        Column("model_id", String(128), nullable=False),
        Column("voice_mode", String(16), nullable=False),
        Column("memory_mode", String(32), nullable=False),
        Column("status", String(32), nullable=False),
        Column("microphone_consent", Boolean, nullable=False),
        Column("screen_consent", Boolean, nullable=False),
        Column("game_audio_consent", Boolean, nullable=False),
        Column("audio_input_ms", BigInteger, nullable=False),
        Column("audio_output_ms", BigInteger, nullable=False),
        Column("video_frame_count", BigInteger, nullable=False),
        Column("interruption_count", BigInteger, nullable=False),
        Column("tool_call_count", BigInteger, nullable=False),
        Column("last_error_code", String(128)),
        Column("started_at", UTCDateTime(), nullable=False),
        Column("ended_at", UTCDateTime()),
        Column("revision", BigInteger, nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_realtime_sessions"),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_core_realtime_sessions_idempotency",
        ),
        CheckConstraint("revision >= 1", name="ck_core_realtime_sessions_revision"),
        CheckConstraint(
            "audio_input_ms >= 0 AND audio_output_ms >= 0 AND video_frame_count >= 0 "
            "AND interruption_count >= 0 AND tool_call_count >= 0",
            name="ck_core_realtime_sessions_usage",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_realtime_sessions_conversation",
        ),
    )
    memories = Table(
        "core_game_memory_observations",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("id", String(36), primary_key=True),
        Column("session_id", String(36), nullable=False),
        Column("game_title", String(160), nullable=False),
        Column("played_at", UTCDateTime(), nullable=False),
        Column("duration_seconds", BigInteger, nullable=False),
        Column("activities", JSON, nullable=False),
        Column("progress_summary", String(800), nullable=False),
        Column("next_goal", String(300)),
        Column("notable_outcome", String(300)),
        Column("accepted", Boolean, nullable=False),
        Column("created_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_game_memory_observations"),
        ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["core_realtime_sessions.tenant_id", "core_realtime_sessions.id"],
            name="fk_core_game_memory_observations_session",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "duration_seconds BETWEEN 0 AND 86400",
            name="ck_core_game_memory_observations_duration",
        ),
    )
    Index(
        "ix_core_realtime_sessions_tenant_started",
        sessions.c.tenant_id,
        sessions.c.started_at,
    )
    Index(
        "ix_core_game_memory_observations_tenant_played",
        memories.c.tenant_id,
        memories.c.played_at,
    )
    return sessions, memories


__all__ = ["build_realtime_tables"]
