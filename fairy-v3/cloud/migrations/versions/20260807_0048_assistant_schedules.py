"""Add durable local Assistant schedules and occurrences.

Revision ID: 20260807_0048
Revises: 20260807_0047
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0048"
down_revision: str | Sequence[str] | None = "20260807_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEDULES = "core_assistant_schedules"
OCCURRENCES = "core_assistant_schedule_occurrences"
TABLES = (SCHEDULES, OCCURRENCES)


def upgrade() -> None:
    op.create_table(
        SCHEDULES,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36)),
        sa.Column("project_id", sa.String(36)),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36)),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("trigger_kind", sa.String(32), nullable=False),
        sa.Column("trigger_rule", sa.JSON(), nullable=False),
        sa.Column("timezone", sa.String(255), nullable=False),
        sa.Column("next_fire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execution_target", sa.String(32), nullable=False),
        sa.Column("profile_id", sa.String(255)),
        sa.Column("model_selection", sa.JSON(none_as_null=True)),
        sa.Column("permission_profile", sa.String(32), nullable=False),
        sa.Column("timeline_sequence", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("active_revision", sa.BigInteger(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_fence", sa.BigInteger(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("attention_code", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_fire_at", sa.DateTime(timezone=True)),
        sa.Column("paused_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_assistant_schedules"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_core_assistant_schedules_idempotency",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "conversation_id",
            "timeline_sequence",
            name="uq_core_assistant_schedules_timeline",
        ),
        sa.CheckConstraint(
            "trigger_kind IN ('once','daily','weekdays','weekly','interval')",
            name="ck_core_assistant_schedules_trigger",
        ),
        sa.CheckConstraint(
            "status IN ('active','paused','completed','cancelled')",
            name="ck_core_assistant_schedules_status",
        ),
        sa.CheckConstraint(
            "execution_target = 'local'",
            name="ck_core_assistant_schedules_local_target",
        ),
        sa.CheckConstraint(
            "permission_profile IN ('observe','standard','autonomous')",
            name="ck_core_assistant_schedules_permission",
        ),
        sa.CheckConstraint(
            "timeline_sequence > 0 AND active_revision > 0 "
            "AND consecutive_failures >= 0 AND lease_fence >= 0",
            name="ck_core_assistant_schedules_counters",
        ),
        sa.CheckConstraint(
            "(profile_id IS NULL) <> (model_selection IS NULL)",
            name="ck_core_assistant_schedules_model_source",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_until IS NULL)",
            name="ck_core_assistant_schedules_lease_pair",
        ),
        sa.CheckConstraint(
            "(status <> 'paused') OR paused_at IS NOT NULL",
            name="ck_core_assistant_schedules_paused_at",
        ),
        sa.CheckConstraint(
            "(status <> 'completed') OR completed_at IS NOT NULL",
            name="ck_core_assistant_schedules_completed_at",
        ),
        sa.CheckConstraint(
            "(status <> 'cancelled') OR cancelled_at IS NOT NULL",
            name="ck_core_assistant_schedules_cancelled_at",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_assistant_schedules_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_core_assistant_schedules_task",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_core_assistant_schedules_project",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["core_workspaces.tenant_id", "core_workspaces.id"],
            name="fk_core_assistant_schedules_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_core_assistant_schedules_version",
        ),
    )
    op.create_table(
        OCCURRENCES,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("schedule_id", sa.String(36), nullable=False),
        sa.Column("schedule_revision", sa.BigInteger(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("coalesced_count", sa.Integer(), nullable=False),
        sa.Column("turn_id", sa.String(36)),
        sa.Column("workflow_run_id", sa.String(36)),
        sa.Column("public_error", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "id",
            name="pk_core_assistant_schedule_occurrences",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "schedule_id",
            "scheduled_for",
            name="uq_core_assistant_schedule_occurrences_instant",
        ),
        sa.CheckConstraint(
            "status IN ('pending','dispatched','succeeded','failed','cancelled',"
            "'attention_required','coalesced')",
            name="ck_core_assistant_schedule_occurrences_status",
        ),
        sa.CheckConstraint(
            "schedule_revision > 0 AND coalesced_count >= 0",
            name="ck_core_assistant_schedule_occurrences_counters",
        ),
        sa.CheckConstraint(
            "(turn_id IS NULL) = (workflow_run_id IS NULL)",
            name="ck_core_assistant_schedule_occurrences_bindings",
        ),
        sa.CheckConstraint(
            "(status <> 'dispatched') OR dispatched_at IS NOT NULL",
            name="ck_core_assistant_schedule_occurrences_dispatched_at",
        ),
        sa.CheckConstraint(
            "(status NOT IN ('succeeded','failed','cancelled','attention_required','coalesced')) "
            "OR completed_at IS NOT NULL",
            name="ck_core_assistant_schedule_occurrences_completed_at",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "schedule_id"],
            [f"{SCHEDULES}.tenant_id", f"{SCHEDULES}.id"],
            name="fk_core_assistant_schedule_occurrences_schedule",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "turn_id"],
            ["core_assistant_turns.tenant_id", "core_assistant_turns.id"],
            name="fk_core_assistant_schedule_occurrences_turn",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workflow_run_id"],
            ["core_workflow_runs.tenant_id", "core_workflow_runs.id"],
            name="fk_core_assistant_schedule_occurrences_workflow",
        ),
    )
    op.create_index(
        "ix_core_assistant_schedules_due",
        SCHEDULES,
        ["tenant_id", "status", "next_fire_at", "lease_until"],
    )
    op.create_index(
        "ix_core_assistant_occurrences_active",
        OCCURRENCES,
        ["tenant_id", "schedule_id", "status", "scheduled_for"],
    )
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
    for table in TABLES:
        op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
        op.execute(
            sa.text(
                f'CREATE POLICY "tenant_isolation_{table}" ON "{table}" '
                f"USING ({predicate}) WITH CHECK ({predicate})"
            )
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(sa.text(f'DROP POLICY IF EXISTS "tenant_isolation_{table}" ON "{table}"'))
        op.execute(sa.text(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY'))
    op.drop_index("ix_core_assistant_occurrences_active", table_name=OCCURRENCES)
    op.drop_index("ix_core_assistant_schedules_due", table_name=SCHEDULES)
    op.drop_table(OCCURRENCES)
    op.drop_table(SCHEDULES)
