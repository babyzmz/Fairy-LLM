from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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

from fairy_core.persistence.tenant import TENANT_ID_LENGTH
from fairy_core.storage.types import UTCDateTime

ID_LENGTH = 36


def build_workflow_schema(
    metadata: MetaData,
    *,
    projects: Table,
    conversations: Table,
    tasks: Table,
) -> tuple[Table, Table, Table, Table, Table, Table]:
    runs = Table(
        "core_workflow_runs",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), nullable=False),
        Column("id", String(ID_LENGTH), nullable=False),
        Column("owner_kind", String(64), nullable=False),
        Column("owner_id", String(255), nullable=False),
        Column("conversation_id", String(ID_LENGTH)),
        Column("task_id", String(ID_LENGTH)),
        Column("project_id", String(ID_LENGTH)),
        Column("execution_target", String(32), nullable=False),
        Column("trigger_kind", String(32), nullable=False),
        Column("parent_run_id", String(ID_LENGTH)),
        Column("status", String(32), nullable=False),
        Column("budget_tier", String(32), nullable=False),
        Column("max_model_rounds", Integer, nullable=False),
        Column("max_tool_invocations", Integer, nullable=False),
        Column("max_duration_seconds", Integer, nullable=False),
        Column("max_parallel_nodes", Integer, nullable=False),
        Column("model_rounds_used", Integer, nullable=False),
        Column("tool_invocations_used", Integer, nullable=False),
        Column("active_plan_revision", BigInteger, nullable=False),
        Column("cancellation_revision", BigInteger, nullable=False),
        Column("engine_version", Integer, nullable=False),
        Column("pause_requested", Boolean, nullable=False),
        Column("idempotency_key", String(512), nullable=False),
        Column("error_code", String(128)),
        Column("created_at", UTCDateTime(), nullable=False),
        Column("updated_at", UTCDateTime(), nullable=False),
        Column("started_at", UTCDateTime()),
        Column("completed_at", UTCDateTime()),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_workflow_runs"),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_core_workflow_runs_tenant_idempotency",
        ),
        UniqueConstraint(
            "tenant_id",
            "owner_kind",
            "owner_id",
            "engine_version",
            name="uq_core_workflow_runs_owner_engine",
        ),
        CheckConstraint(
            "status IN ('queued','running','waiting_for_approval','waiting_for_input','paused',"
            "'completed','cancelled','failed')",
            name="ck_core_workflow_runs_status",
        ),
        CheckConstraint(
            "execution_target IN ('local','cloud')",
            name="ck_core_workflow_runs_execution_target",
        ),
        CheckConstraint(
            "trigger_kind IN ('user_turn','manual','recovery','domain')",
            name="ck_core_workflow_runs_trigger_kind",
        ),
        CheckConstraint(
            "budget_tier IN ('normal','deep')",
            name="ck_core_workflow_runs_budget_tier",
        ),
        CheckConstraint(
            "max_model_rounds > 0 AND max_tool_invocations > 0 "
            "AND max_duration_seconds > 0 AND max_parallel_nodes > 0",
            name="ck_core_workflow_runs_budget",
        ),
        CheckConstraint(
            "model_rounds_used >= 0 AND model_rounds_used <= max_model_rounds "
            "AND tool_invocations_used >= 0 "
            "AND tool_invocations_used <= max_tool_invocations",
            name="ck_core_workflow_runs_budget_usage",
        ),
        CheckConstraint(
            "active_plan_revision > 0 AND cancellation_revision >= 0 AND engine_version > 0",
            name="ck_core_workflow_runs_revisions",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            [conversations.c.tenant_id, conversations.c.id],
            name="fk_core_workflow_runs_conversation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            [tasks.c.tenant_id, tasks.c.id],
            name="fk_core_workflow_runs_task",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            [projects.c.tenant_id, projects.c.id],
            name="fk_core_workflow_runs_project",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "parent_run_id"],
            ["core_workflow_runs.tenant_id", "core_workflow_runs.id"],
            name="fk_core_workflow_runs_parent",
        ),
    )

    revisions = Table(
        "core_workflow_plan_revisions",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), nullable=False),
        Column("run_id", String(ID_LENGTH), nullable=False),
        Column("revision", BigInteger, nullable=False),
        Column("reason", String(64), nullable=False),
        Column("instruction_id", String(ID_LENGTH)),
        Column("created_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint(
            "tenant_id",
            "run_id",
            "revision",
            name="pk_core_workflow_plan_revisions",
        ),
        CheckConstraint("revision > 0", name="ck_core_workflow_plan_revisions_revision"),
        CheckConstraint(
            "reason IN ('initial','steering','recovery','repair')",
            name="ck_core_workflow_plan_revisions_reason",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [runs.c.tenant_id, runs.c.id],
            name="fk_core_workflow_plan_revisions_run",
            ondelete="CASCADE",
        ),
    )

    nodes = Table(
        "core_workflow_nodes",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), nullable=False),
        Column("id", String(ID_LENGTH), nullable=False),
        Column("run_id", String(ID_LENGTH), nullable=False),
        Column("plan_revision", BigInteger, nullable=False),
        Column("node_key", String(255), nullable=False),
        Column("kind", String(128), nullable=False),
        Column("payload", JSON, nullable=False),
        Column("payload_version", Integer, nullable=False),
        Column("status", String(32), nullable=False),
        Column("concurrency_policy", String(32), nullable=False),
        Column("resource_keys", JSON, nullable=False),
        Column("max_attempts", Integer, nullable=False),
        Column("attempt_count", Integer, nullable=False),
        Column("available_at", UTCDateTime(), nullable=False),
        Column("public_summary", String(500), nullable=False),
        Column("result", JSON),
        Column("evidence_refs", JSON, nullable=False),
        Column("error_code", String(128)),
        Column("created_at", UTCDateTime(), nullable=False),
        Column("updated_at", UTCDateTime(), nullable=False),
        Column("started_at", UTCDateTime()),
        Column("completed_at", UTCDateTime()),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_workflow_nodes"),
        UniqueConstraint(
            "tenant_id",
            "run_id",
            "plan_revision",
            "node_key",
            name="uq_core_workflow_nodes_plan_key",
        ),
        CheckConstraint(
            "status IN ('pending','ready','running','waiting_for_approval','waiting_for_input',"
            "'succeeded',"
            "'failed','cancelled','skipped','superseded')",
            name="ck_core_workflow_nodes_status",
        ),
        CheckConstraint(
            "concurrency_policy IN ('serial','parallel_read')",
            name="ck_core_workflow_nodes_concurrency",
        ),
        CheckConstraint(
            "plan_revision > 0 AND payload_version > 0 AND max_attempts > 0 "
            "AND attempt_count >= 0 AND attempt_count <= max_attempts",
            name="ck_core_workflow_nodes_counters",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id", "plan_revision"],
            [revisions.c.tenant_id, revisions.c.run_id, revisions.c.revision],
            name="fk_core_workflow_nodes_plan",
            ondelete="CASCADE",
        ),
    )

    edges = Table(
        "core_workflow_edges",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), nullable=False),
        Column("run_id", String(ID_LENGTH), nullable=False),
        Column("plan_revision", BigInteger, nullable=False),
        Column("from_node_id", String(ID_LENGTH), nullable=False),
        Column("to_node_id", String(ID_LENGTH), nullable=False),
        PrimaryKeyConstraint(
            "tenant_id",
            "run_id",
            "plan_revision",
            "from_node_id",
            "to_node_id",
            name="pk_core_workflow_edges",
        ),
        CheckConstraint(
            "from_node_id <> to_node_id",
            name="ck_core_workflow_edges_distinct",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id", "plan_revision"],
            [revisions.c.tenant_id, revisions.c.run_id, revisions.c.revision],
            name="fk_core_workflow_edges_plan",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "from_node_id"],
            [nodes.c.tenant_id, nodes.c.id],
            name="fk_core_workflow_edges_from_node",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "to_node_id"],
            [nodes.c.tenant_id, nodes.c.id],
            name="fk_core_workflow_edges_to_node",
            ondelete="CASCADE",
        ),
    )

    attempts = Table(
        "core_workflow_attempts",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), nullable=False),
        Column("node_id", String(ID_LENGTH), nullable=False),
        Column("attempt_number", Integer, nullable=False),
        Column("run_id", String(ID_LENGTH), nullable=False),
        Column("status", String(32), nullable=False),
        Column("lease_owner", String(128)),
        Column("lease_fence", BigInteger, nullable=False),
        Column("lease_until", UTCDateTime()),
        Column("cancellation_revision", BigInteger, nullable=False),
        Column("plan_revision", BigInteger, nullable=False),
        Column("result", JSON),
        Column("evidence_refs", JSON, nullable=False),
        Column("error_code", String(128)),
        Column("started_at", UTCDateTime(), nullable=False),
        Column("finished_at", UTCDateTime()),
        PrimaryKeyConstraint(
            "tenant_id",
            "node_id",
            "attempt_number",
            name="pk_core_workflow_attempts",
        ),
        CheckConstraint(
            "status IN ('running','succeeded','failed','abandoned','cancelled','waiting')",
            name="ck_core_workflow_attempts_status",
        ),
        CheckConstraint(
            "attempt_number > 0 AND lease_fence > 0 AND cancellation_revision >= 0 "
            "AND plan_revision > 0",
            name="ck_core_workflow_attempts_counters",
        ),
        CheckConstraint(
            "(status = 'running' AND lease_owner IS NOT NULL AND lease_until IS NOT NULL) OR "
            "(status <> 'running' AND lease_owner IS NULL AND lease_until IS NULL)",
            name="ck_core_workflow_attempts_lease",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "node_id"],
            [nodes.c.tenant_id, nodes.c.id],
            name="fk_core_workflow_attempts_node",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [runs.c.tenant_id, runs.c.id],
            name="fk_core_workflow_attempts_run",
            ondelete="CASCADE",
        ),
    )

    instructions = Table(
        "core_workflow_instructions",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), nullable=False),
        Column("id", String(ID_LENGTH), nullable=False),
        Column("run_id", String(ID_LENGTH), nullable=False),
        Column("idempotency_key", String(512), nullable=False),
        Column("instruction", Text, nullable=False),
        Column("expected_revision", BigInteger, nullable=False),
        Column("status", String(32), nullable=False),
        Column("applied_revision", BigInteger),
        Column("created_at", UTCDateTime(), nullable=False),
        Column("applied_at", UTCDateTime()),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_workflow_instructions"),
        UniqueConstraint(
            "tenant_id",
            "run_id",
            "idempotency_key",
            name="uq_core_workflow_instructions_idempotency",
        ),
        CheckConstraint(
            "status IN ('pending','applied','rejected')",
            name="ck_core_workflow_instructions_status",
        ),
        CheckConstraint(
            "expected_revision > 0 AND (applied_revision IS NULL OR applied_revision > 0)",
            name="ck_core_workflow_instructions_revision",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [runs.c.tenant_id, runs.c.id],
            name="fk_core_workflow_instructions_run",
            ondelete="CASCADE",
        ),
    )

    Index(
        "ix_core_workflow_runs_claim",
        runs.c.tenant_id,
        runs.c.status,
        runs.c.pause_requested,
        runs.c.updated_at,
    )
    Index(
        "ix_core_workflow_nodes_claim",
        nodes.c.tenant_id,
        nodes.c.status,
        nodes.c.available_at,
        nodes.c.run_id,
    )
    Index(
        "ix_core_workflow_attempts_lease",
        attempts.c.tenant_id,
        attempts.c.status,
        attempts.c.lease_until,
    )
    return runs, revisions, nodes, edges, attempts, instructions


__all__ = ["build_workflow_schema"]
