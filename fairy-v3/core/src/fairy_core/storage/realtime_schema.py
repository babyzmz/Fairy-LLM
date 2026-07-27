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
    Text,
    UniqueConstraint,
)

from fairy_core.persistence.tenant import TENANT_ID_LENGTH
from fairy_core.storage.types import UTCDateTime


def build_realtime_tables(metadata: MetaData) -> tuple[Table, Table, Table, Table, Table]:
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
    digests = Table(
        "core_companion_session_digests",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("id", String(36), primary_key=True),
        Column("session_id", String(36), nullable=False),
        Column("conversation_id", String(36), nullable=False),
        Column("request_id", String(128), nullable=False),
        Column("request_fingerprint", String(64), nullable=False),
        Column("activity", String(16), nullable=False),
        Column("subject_title", String(160)),
        Column("started_at", UTCDateTime(), nullable=False),
        Column("ended_at", UTCDateTime(), nullable=False),
        Column("duration_seconds", BigInteger, nullable=False),
        Column("activities", JSON, nullable=False),
        Column("progress_summary", String(1_200), nullable=False),
        Column("unresolved_issue", String(500)),
        Column("next_goal", String(500)),
        Column("notable_outcome", String(500)),
        Column("source_first_sequence", BigInteger, nullable=False),
        Column("source_last_sequence", BigInteger, nullable=False),
        Column("source_digest", String(64), nullable=False),
        Column("policy_version", String(64), nullable=False),
        Column("proposal_ids", JSON, nullable=False),
        Column("created_at", UTCDateTime(), nullable=False),
        Column("revision", BigInteger, nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_companion_session_digests"),
        UniqueConstraint(
            "tenant_id",
            "session_id",
            "request_id",
            name="uq_core_companion_session_digests_request",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["core_realtime_sessions.tenant_id", "core_realtime_sessions.id"],
            name="fk_core_companion_session_digests_session",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_companion_session_digests_conversation",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "activity IN ('auto', 'game', 'focus')",
            name="ck_core_companion_session_digests_activity",
        ),
        CheckConstraint(
            "duration_seconds BETWEEN 0 AND 86400",
            name="ck_core_companion_session_digests_duration",
        ),
        CheckConstraint(
            "source_first_sequence >= 1 AND source_last_sequence >= source_first_sequence",
            name="ck_core_companion_session_digests_source_range",
        ),
        CheckConstraint("revision >= 1", name="ck_core_companion_session_digests_revision"),
    )
    # Local-only, reviewable transcript of a voice session's stable public
    # captions. Raw audio, frames, VAD, partial captions, and hidden reasoning
    # are never stored here; see ADR 0018.
    transcript = Table(
        "core_realtime_transcript_entries",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("id", String(36), primary_key=True),
        Column("session_id", String(36), nullable=False),
        Column("conversation_id", String(36), nullable=False),
        Column("sequence", BigInteger, nullable=False),
        Column("speaker", String(16), nullable=False),
        Column("text", String, nullable=False),
        Column("created_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_realtime_transcript_entries"),
        UniqueConstraint(
            "tenant_id",
            "session_id",
            "sequence",
            name="uq_core_realtime_transcript_sequence",
        ),
        CheckConstraint("sequence >= 1", name="ck_core_realtime_transcript_sequence"),
        CheckConstraint(
            "speaker IN ('user', 'assistant')",
            name="ck_core_realtime_transcript_speaker",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["core_realtime_sessions.tenant_id", "core_realtime_sessions.id"],
            name="fk_core_realtime_transcript_session",
            ondelete="CASCADE",
        ),
    )
    assistance = Table(
        "core_realtime_assistance",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("id", String(36), primary_key=True),
        Column("session_id", String(36), nullable=False),
        Column("conversation_id", String(36), nullable=False),
        Column("request_id", String(128), nullable=False),
        Column("request_fingerprint", String(64), nullable=False),
        Column("segment_id", String(128), nullable=False),
        Column("context_epoch", BigInteger, nullable=False),
        Column("question", String(4_000), nullable=False),
        Column("activity_profile", String(32), nullable=False),
        Column("application_title", String(128)),
        Column("observed_facts", JSON, nullable=False),
        Column("allow_network", Boolean, nullable=False),
        Column("locale", String(32), nullable=False),
        Column("status", String(32), nullable=False),
        Column("task_id", String(36)),
        Column("turn_id", String(36)),
        Column("message_id", String(36)),
        Column("spoken_summary", String(320)),
        Column("display_markdown", Text),
        Column("citations", JSON, nullable=False),
        Column("freshness", String(128)),
        Column("requires_user_confirmation", Boolean, nullable=False),
        Column("error_code", String(128)),
        Column("created_at", UTCDateTime(), nullable=False),
        Column("updated_at", UTCDateTime(), nullable=False),
        Column("revision", BigInteger, nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_realtime_assistance"),
        UniqueConstraint(
            "tenant_id",
            "session_id",
            "request_id",
            name="uq_core_realtime_assistance_request",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["core_realtime_sessions.tenant_id", "core_realtime_sessions.id"],
            name="fk_core_realtime_assistance_session",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_realtime_assistance_conversation",
            ondelete="CASCADE",
        ),
        CheckConstraint("context_epoch >= 1", name="ck_core_realtime_assistance_epoch"),
        CheckConstraint("revision >= 1", name="ck_core_realtime_assistance_revision"),
        CheckConstraint(
            "status IN ('queued', 'running', 'awaiting_approval', 'completed', "
            "'failed', 'cancelled')",
            name="ck_core_realtime_assistance_status",
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
    Index(
        "ix_core_companion_session_digests_session_created",
        digests.c.tenant_id,
        digests.c.session_id,
        digests.c.created_at,
    )
    Index(
        "ix_core_realtime_transcript_conversation",
        transcript.c.tenant_id,
        transcript.c.conversation_id,
        transcript.c.created_at,
    )
    Index(
        "ix_core_realtime_assistance_session_status",
        assistance.c.tenant_id,
        assistance.c.session_id,
        assistance.c.status,
        assistance.c.created_at,
    )
    Index(
        "ix_core_realtime_assistance_conversation",
        assistance.c.tenant_id,
        assistance.c.conversation_id,
        assistance.c.created_at,
    )
    return sessions, memories, digests, transcript, assistance


__all__ = ["build_realtime_tables"]
