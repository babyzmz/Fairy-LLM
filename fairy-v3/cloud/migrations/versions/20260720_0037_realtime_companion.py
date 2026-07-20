"""Add privacy-safe realtime companion sessions and game memory observations.

Revision ID: 20260720_0037
Revises: 20260717_0036
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0037"
down_revision: str | Sequence[str] | None = "20260717_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SESSION_TABLE = "core_realtime_sessions"
MEMORY_TABLE = "core_game_memory_observations"


def upgrade() -> None:
    op.create_table(
        "core_realtime_sessions",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("device_id", sa.String(128), nullable=False),
        sa.Column("conversation_id", sa.String(36)),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("voice_mode", sa.String(16), nullable=False),
        sa.Column("memory_mode", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("microphone_consent", sa.Boolean(), nullable=False),
        sa.Column("screen_consent", sa.Boolean(), nullable=False),
        sa.Column("game_audio_consent", sa.Boolean(), nullable=False),
        sa.Column("audio_input_ms", sa.BigInteger(), nullable=False),
        sa.Column("audio_output_ms", sa.BigInteger(), nullable=False),
        sa.Column("video_frame_count", sa.BigInteger(), nullable=False),
        sa.Column("interruption_count", sa.BigInteger(), nullable=False),
        sa.Column("tool_call_count", sa.BigInteger(), nullable=False),
        sa.Column("last_error_code", sa.String(128)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_realtime_sessions"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_core_realtime_sessions_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_realtime_sessions_conversation",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_core_realtime_sessions_revision"),
        sa.CheckConstraint(
            "audio_input_ms >= 0 AND audio_output_ms >= 0 "
            "AND video_frame_count >= 0 AND interruption_count >= 0 "
            "AND tool_call_count >= 0",
            name="ck_core_realtime_sessions_usage",
        ),
    )
    op.create_index(
        "ix_core_realtime_sessions_tenant_started",
        "core_realtime_sessions",
        ["tenant_id", "started_at"],
    )
    op.create_table(
        "core_game_memory_observations",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("game_title", sa.String(160), nullable=False),
        sa.Column("played_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.BigInteger(), nullable=False),
        sa.Column("activities", sa.JSON(), nullable=False),
        sa.Column("progress_summary", sa.String(800), nullable=False),
        sa.Column("next_goal", sa.String(300)),
        sa.Column("notable_outcome", sa.String(300)),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id", "id", name="pk_core_game_memory_observations"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["core_realtime_sessions.tenant_id", "core_realtime_sessions.id"],
            name="fk_core_game_memory_observations_session",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "duration_seconds BETWEEN 0 AND 86400",
            name="ck_core_game_memory_observations_duration",
        ),
    )
    op.create_index(
        "ix_core_game_memory_observations_tenant_played",
        "core_game_memory_observations",
        ["tenant_id", "played_at"],
    )
    _enable_rls(SESSION_TABLE)
    _enable_rls(MEMORY_TABLE)


def downgrade() -> None:
    for table_name in (MEMORY_TABLE, SESSION_TABLE):
        op.execute(
            sa.text(f'DROP POLICY IF EXISTS "tenant_isolation_{table_name}" ON "{table_name}"')
        )
        op.execute(sa.text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))
    op.drop_index(
        "ix_core_game_memory_observations_tenant_played",
        table_name="core_game_memory_observations",
    )
    op.drop_table("core_game_memory_observations")
    op.drop_index(
        "ix_core_realtime_sessions_tenant_started",
        table_name="core_realtime_sessions",
    )
    op.drop_table("core_realtime_sessions")


def _enable_rls(table_name: str) -> None:
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
    op.execute(sa.text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            f'CREATE POLICY "tenant_isolation_{table_name}" ON "{table_name}" '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    )
