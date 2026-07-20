"""Add durable assistant Turn Traces.

Revision ID: 20260715_0032
Revises: 20260715_0031
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_0032"
down_revision: str | Sequence[str] | None = "20260715_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRACE_TABLE = "core_turn_traces"
STEP_TABLE = "core_turn_trace_steps"


def upgrade() -> None:
    op.create_table(
        TRACE_TABLE,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("turn_id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("legacy", sa.Boolean(), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_turn_traces"),
        sa.UniqueConstraint("tenant_id", "turn_id", name="uq_core_turn_traces_turn"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            "turn_id",
            name="uq_core_turn_traces_scope",
        ),
        sa.CheckConstraint("last_sequence >= 0", name="ck_core_turn_traces_sequence"),
        sa.CheckConstraint("revision >= 0", name="ck_core_turn_traces_revision"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "turn_id", "conversation_id", "task_id"],
            [
                "core_assistant_turns.tenant_id",
                "core_assistant_turns.id",
                "core_assistant_turns.conversation_id",
                "core_assistant_turns.task_id",
            ],
            name="fk_core_turn_traces_turn_scope",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        STEP_TABLE,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("trace_id", sa.String(36), nullable=False),
        sa.Column("turn_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("parent_step_id", sa.String(36)),
        sa.Column("caused_by_step_id", sa.String(36)),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("public_summary", sa.String(512), nullable=False),
        sa.Column("public_detail", sa.String(4000)),
        sa.Column("model_id", sa.String(255)),
        sa.Column("model_role", sa.String(32)),
        sa.Column("provider_attempt_id", sa.String(36)),
        sa.Column("command_run_id", sa.String(36)),
        sa.Column("artifact_refs", sa.JSON(), nullable=False),
        sa.Column("visibility", sa.String(32), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_turn_trace_steps"),
        sa.UniqueConstraint(
            "tenant_id",
            "trace_id",
            "sequence",
            name="uq_core_turn_trace_steps_sequence",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "trace_id",
            "id",
            name="uq_core_turn_trace_steps_scope",
        ),
        sa.CheckConstraint("sequence > 0", name="ck_core_turn_trace_steps_sequence"),
        sa.CheckConstraint("revision >= 0", name="ck_core_turn_trace_steps_revision"),
        sa.CheckConstraint(
            "kind IN ('route', 'plan', 'reasoning', 'model', 'tool', 'approval', "
            "'observation', 'verification', 'artifact', 'response', 'voice')",
            name="ck_core_turn_trace_steps_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'waiting', 'succeeded', 'failed', "
            "'cancelled', 'skipped')",
            name="ck_core_turn_trace_steps_status",
        ),
        sa.CheckConstraint(
            "visibility IN ('user', 'developer', 'internal')",
            name="ck_core_turn_trace_steps_visibility",
        ),
        sa.CheckConstraint(
            "model_role IS NULL OR model_role IN ('coordinator', 'primary', 'reviewer')",
            name="ck_core_turn_trace_steps_model_role",
        ),
        sa.CheckConstraint(
            "(model_id IS NULL) = (model_role IS NULL)",
            name="ck_core_turn_trace_steps_model_binding",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "trace_id", "turn_id"],
            [
                "core_turn_traces.tenant_id",
                "core_turn_traces.id",
                "core_turn_traces.turn_id",
            ],
            name="fk_core_turn_trace_steps_trace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "provider_attempt_id"],
            ["core_assistant_provider_attempts.tenant_id", "core_assistant_provider_attempts.id"],
            name="fk_core_turn_trace_steps_provider_attempt",
        ),
    )
    op.create_index(
        "ix_core_turn_trace_steps_turn",
        STEP_TABLE,
        ["tenant_id", "turn_id", "sequence"],
    )
    op.create_index(
        "ix_core_turn_trace_steps_command",
        STEP_TABLE,
        ["tenant_id", "command_run_id"],
    )
    _enable_rls(TRACE_TABLE)
    _enable_rls(STEP_TABLE)


def downgrade() -> None:
    for table_name in (STEP_TABLE, TRACE_TABLE):
        op.execute(
            sa.text(f'DROP POLICY IF EXISTS "tenant_isolation_{table_name}" ON "{table_name}"')
        )
        op.execute(sa.text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))
    op.drop_index("ix_core_turn_trace_steps_command", table_name=STEP_TABLE)
    op.drop_index("ix_core_turn_trace_steps_turn", table_name=STEP_TABLE)
    op.drop_table(STEP_TABLE)
    op.drop_table(TRACE_TABLE)


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
