from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from fairy_core.storage.types import UTCDateTime

ID_LENGTH = 36
TENANT_ID_LENGTH = 128


def build_assistant_schedule_schema(
    metadata: MetaData,
    *,
    conversations: Table,
    tasks: Table,
    projects: Table,
    workspaces: Table,
    versions: Table,
    assistant_turns: Table,
    workflow_runs: Table,
) -> tuple[Table, Table]:
    schedules = Table(
        "core_assistant_schedules",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), nullable=False),
        Column("id", String(ID_LENGTH), nullable=False),
        Column("conversation_id", String(ID_LENGTH), nullable=False),
        Column("task_id", String(ID_LENGTH)),
        Column("project_id", String(ID_LENGTH)),
        Column("workspace_id", String(ID_LENGTH), nullable=False),
        Column("version_id", String(ID_LENGTH)),
        Column("instruction", Text, nullable=False),
        Column("trigger_kind", String(32), nullable=False),
        Column("trigger_rule", JSON, nullable=False),
        Column("timezone", String(255), nullable=False),
        Column("next_fire_at", UTCDateTime(), nullable=False),
        Column("execution_target", String(32), nullable=False),
        Column("profile_id", String(255)),
        Column("model_selection", JSON(none_as_null=True)),
        Column("permission_profile", String(32), nullable=False),
        Column("timeline_sequence", BigInteger, nullable=False),
        Column("status", String(32), nullable=False),
        Column("active_revision", BigInteger, nullable=False),
        Column("consecutive_failures", Integer, nullable=False),
        Column("idempotency_key", String(512), nullable=False),
        Column("lease_owner", String(128)),
        Column("lease_fence", BigInteger, nullable=False),
        Column("lease_until", UTCDateTime()),
        Column("attention_code", String(128)),
        Column("created_at", UTCDateTime(), nullable=False),
        Column("updated_at", UTCDateTime(), nullable=False),
        Column("last_fire_at", UTCDateTime()),
        Column("paused_at", UTCDateTime()),
        Column("completed_at", UTCDateTime()),
        Column("cancelled_at", UTCDateTime()),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_assistant_schedules"),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_core_assistant_schedules_idempotency",
        ),
        UniqueConstraint(
            "tenant_id",
            "conversation_id",
            "timeline_sequence",
            name="uq_core_assistant_schedules_timeline",
        ),
        CheckConstraint(
            "trigger_kind IN ('once','daily','weekdays','weekly','interval')",
            name="ck_core_assistant_schedules_trigger",
        ),
        CheckConstraint(
            "status IN ('active','paused','completed','cancelled')",
            name="ck_core_assistant_schedules_status",
        ),
        CheckConstraint(
            "execution_target = 'local'",
            name="ck_core_assistant_schedules_local_target",
        ),
        CheckConstraint(
            "permission_profile IN ('observe','standard','autonomous')",
            name="ck_core_assistant_schedules_permission",
        ),
        CheckConstraint(
            "timeline_sequence > 0 AND active_revision > 0 "
            "AND consecutive_failures >= 0 AND lease_fence >= 0",
            name="ck_core_assistant_schedules_counters",
        ),
        CheckConstraint(
            "(profile_id IS NULL) <> (model_selection IS NULL)",
            name="ck_core_assistant_schedules_model_source",
        ),
        CheckConstraint(
            "(lease_owner IS NULL) = (lease_until IS NULL)",
            name="ck_core_assistant_schedules_lease_pair",
        ),
        CheckConstraint(
            "(status <> 'paused') OR paused_at IS NOT NULL",
            name="ck_core_assistant_schedules_paused_at",
        ),
        CheckConstraint(
            "(status <> 'completed') OR completed_at IS NOT NULL",
            name="ck_core_assistant_schedules_completed_at",
        ),
        CheckConstraint(
            "(status <> 'cancelled') OR cancelled_at IS NOT NULL",
            name="ck_core_assistant_schedules_cancelled_at",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            [conversations.c.tenant_id, conversations.c.id],
            name="fk_core_assistant_schedules_conversation",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            [tasks.c.tenant_id, tasks.c.id],
            name="fk_core_assistant_schedules_task",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            [projects.c.tenant_id, projects.c.id],
            name="fk_core_assistant_schedules_project",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            [workspaces.c.tenant_id, workspaces.c.id],
            name="fk_core_assistant_schedules_workspace",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            [versions.c.tenant_id, versions.c.id],
            name="fk_core_assistant_schedules_version",
        ),
    )
    occurrences = Table(
        "core_assistant_schedule_occurrences",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), nullable=False),
        Column("id", String(ID_LENGTH), nullable=False),
        Column("schedule_id", String(ID_LENGTH), nullable=False),
        Column("schedule_revision", BigInteger, nullable=False),
        Column("scheduled_for", UTCDateTime(), nullable=False),
        Column("status", String(32), nullable=False),
        Column("coalesced_count", Integer, nullable=False),
        Column("turn_id", String(ID_LENGTH)),
        Column("workflow_run_id", String(ID_LENGTH)),
        Column("public_error", String(500)),
        Column("created_at", UTCDateTime(), nullable=False),
        Column("dispatched_at", UTCDateTime()),
        Column("completed_at", UTCDateTime()),
        PrimaryKeyConstraint(
            "tenant_id",
            "id",
            name="pk_core_assistant_schedule_occurrences",
        ),
        UniqueConstraint(
            "tenant_id",
            "schedule_id",
            "scheduled_for",
            name="uq_core_assistant_schedule_occurrences_instant",
        ),
        CheckConstraint(
            "status IN ('pending','dispatched','succeeded','failed','cancelled',"
            "'attention_required','coalesced')",
            name="ck_core_assistant_schedule_occurrences_status",
        ),
        CheckConstraint(
            "schedule_revision > 0 AND coalesced_count >= 0",
            name="ck_core_assistant_schedule_occurrences_counters",
        ),
        CheckConstraint(
            "(turn_id IS NULL) = (workflow_run_id IS NULL)",
            name="ck_core_assistant_schedule_occurrences_bindings",
        ),
        CheckConstraint(
            "(status <> 'dispatched') OR dispatched_at IS NOT NULL",
            name="ck_core_assistant_schedule_occurrences_dispatched_at",
        ),
        CheckConstraint(
            "(status NOT IN ('succeeded','failed','cancelled','attention_required','coalesced')) "
            "OR completed_at IS NOT NULL",
            name="ck_core_assistant_schedule_occurrences_completed_at",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "schedule_id"],
            [schedules.c.tenant_id, schedules.c.id],
            name="fk_core_assistant_schedule_occurrences_schedule",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "turn_id"],
            [assistant_turns.c.tenant_id, assistant_turns.c.id],
            name="fk_core_assistant_schedule_occurrences_turn",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workflow_run_id"],
            [workflow_runs.c.tenant_id, workflow_runs.c.id],
            name="fk_core_assistant_schedule_occurrences_workflow",
        ),
    )
    Index(
        "ix_core_assistant_schedules_due",
        schedules.c.tenant_id,
        schedules.c.status,
        schedules.c.next_fire_at,
        schedules.c.lease_until,
    )
    Index(
        "ix_core_assistant_occurrences_active",
        occurrences.c.tenant_id,
        occurrences.c.schedule_id,
        occurrences.c.status,
        occurrences.c.scheduled_for,
    )
    return schedules, occurrences


__all__ = ["build_assistant_schedule_schema"]
