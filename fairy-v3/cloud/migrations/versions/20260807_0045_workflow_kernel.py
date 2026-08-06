"""Add the durable Workflow Kernel ledger.

Revision ID: 20260807_0045
Revises: 20260806_0044
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0045"
down_revision: str | Sequence[str] | None = "20260806_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNS = "core_workflow_runs"
REVISIONS = "core_workflow_plan_revisions"
NODES = "core_workflow_nodes"
EDGES = "core_workflow_edges"
ATTEMPTS = "core_workflow_attempts"
INSTRUCTIONS = "core_workflow_instructions"
TABLES = (RUNS, REVISIONS, NODES, EDGES, ATTEMPTS, INSTRUCTIONS)


def upgrade() -> None:
    op.create_table(
        RUNS,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("owner_kind", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("conversation_id", sa.String(36)),
        sa.Column("task_id", sa.String(36)),
        sa.Column("project_id", sa.String(36)),
        sa.Column("execution_target", sa.String(32), nullable=False),
        sa.Column("trigger_kind", sa.String(32), nullable=False),
        sa.Column("parent_run_id", sa.String(36)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("budget_tier", sa.String(32), nullable=False),
        sa.Column("max_model_rounds", sa.Integer(), nullable=False),
        sa.Column("max_tool_invocations", sa.Integer(), nullable=False),
        sa.Column("max_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("max_parallel_nodes", sa.Integer(), nullable=False),
        sa.Column("model_rounds_used", sa.Integer(), nullable=False),
        sa.Column("tool_invocations_used", sa.Integer(), nullable=False),
        sa.Column("active_plan_revision", sa.BigInteger(), nullable=False),
        sa.Column("cancellation_revision", sa.BigInteger(), nullable=False),
        sa.Column("engine_version", sa.Integer(), nullable=False),
        sa.Column("pause_requested", sa.Boolean(), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_workflow_runs"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_core_workflow_runs_tenant_idempotency",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "owner_kind",
            "owner_id",
            "engine_version",
            name="uq_core_workflow_runs_owner_engine",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','waiting_for_approval','paused',"
            "'completed','cancelled','failed')",
            name="ck_core_workflow_runs_status",
        ),
        sa.CheckConstraint(
            "execution_target IN ('local','cloud')",
            name="ck_core_workflow_runs_execution_target",
        ),
        sa.CheckConstraint(
            "trigger_kind IN ('user_turn','manual','recovery','domain')",
            name="ck_core_workflow_runs_trigger_kind",
        ),
        sa.CheckConstraint(
            "budget_tier IN ('normal','deep')",
            name="ck_core_workflow_runs_budget_tier",
        ),
        sa.CheckConstraint(
            "max_model_rounds > 0 AND max_tool_invocations > 0 "
            "AND max_duration_seconds > 0 AND max_parallel_nodes > 0",
            name="ck_core_workflow_runs_budget",
        ),
        sa.CheckConstraint(
            "model_rounds_used >= 0 AND model_rounds_used <= max_model_rounds "
            "AND tool_invocations_used >= 0 "
            "AND tool_invocations_used <= max_tool_invocations",
            name="ck_core_workflow_runs_budget_usage",
        ),
        sa.CheckConstraint(
            "active_plan_revision > 0 AND cancellation_revision >= 0 AND engine_version > 0",
            name="ck_core_workflow_runs_revisions",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_workflow_runs_conversation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_core_workflow_runs_task",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_core_workflow_runs_project",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_run_id"],
            [f"{RUNS}.tenant_id", f"{RUNS}.id"],
            name="fk_core_workflow_runs_parent",
        ),
    )
    op.create_table(
        REVISIONS,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("instruction_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "run_id",
            "revision",
            name="pk_core_workflow_plan_revisions",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_core_workflow_plan_revisions_revision",
        ),
        sa.CheckConstraint(
            "reason IN ('initial','steering','recovery','repair')",
            name="ck_core_workflow_plan_revisions_reason",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [f"{RUNS}.tenant_id", f"{RUNS}.id"],
            name="fk_core_workflow_plan_revisions_run",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        NODES,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("plan_revision", sa.BigInteger(), nullable=False),
        sa.Column("node_key", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("concurrency_policy", sa.String(32), nullable=False),
        sa.Column("resource_keys", sa.JSON(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("public_summary", sa.String(500), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_workflow_nodes"),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "plan_revision",
            "node_key",
            name="uq_core_workflow_nodes_plan_key",
        ),
        sa.CheckConstraint(
            "status IN ('pending','ready','running','waiting_for_approval','succeeded',"
            "'failed','cancelled','skipped','superseded')",
            name="ck_core_workflow_nodes_status",
        ),
        sa.CheckConstraint(
            "concurrency_policy IN ('serial','parallel_read')",
            name="ck_core_workflow_nodes_concurrency",
        ),
        sa.CheckConstraint(
            "plan_revision > 0 AND payload_version > 0 AND max_attempts > 0 "
            "AND attempt_count >= 0 AND attempt_count <= max_attempts",
            name="ck_core_workflow_nodes_counters",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id", "plan_revision"],
            [f"{REVISIONS}.tenant_id", f"{REVISIONS}.run_id", f"{REVISIONS}.revision"],
            name="fk_core_workflow_nodes_plan",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        EDGES,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("plan_revision", sa.BigInteger(), nullable=False),
        sa.Column("from_node_id", sa.String(36), nullable=False),
        sa.Column("to_node_id", sa.String(36), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "run_id",
            "plan_revision",
            "from_node_id",
            "to_node_id",
            name="pk_core_workflow_edges",
        ),
        sa.CheckConstraint(
            "from_node_id <> to_node_id",
            name="ck_core_workflow_edges_distinct",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id", "plan_revision"],
            [f"{REVISIONS}.tenant_id", f"{REVISIONS}.run_id", f"{REVISIONS}.revision"],
            name="fk_core_workflow_edges_plan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "from_node_id"],
            [f"{NODES}.tenant_id", f"{NODES}.id"],
            name="fk_core_workflow_edges_from_node",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "to_node_id"],
            [f"{NODES}.tenant_id", f"{NODES}.id"],
            name="fk_core_workflow_edges_to_node",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        ATTEMPTS,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("node_id", sa.String(36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_fence", sa.BigInteger(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("cancellation_revision", sa.BigInteger(), nullable=False),
        sa.Column("plan_revision", sa.BigInteger(), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "node_id",
            "attempt_number",
            name="pk_core_workflow_attempts",
        ),
        sa.CheckConstraint(
            "status IN ('running','succeeded','failed','abandoned','cancelled','waiting')",
            name="ck_core_workflow_attempts_status",
        ),
        sa.CheckConstraint(
            "attempt_number > 0 AND lease_fence > 0 AND cancellation_revision >= 0 "
            "AND plan_revision > 0",
            name="ck_core_workflow_attempts_counters",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND lease_owner IS NOT NULL AND lease_until IS NOT NULL) OR "
            "(status <> 'running' AND lease_owner IS NULL AND lease_until IS NULL)",
            name="ck_core_workflow_attempts_lease",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "node_id"],
            [f"{NODES}.tenant_id", f"{NODES}.id"],
            name="fk_core_workflow_attempts_node",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [f"{RUNS}.tenant_id", f"{RUNS}.id"],
            name="fk_core_workflow_attempts_run",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        INSTRUCTIONS,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("expected_revision", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("applied_revision", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_workflow_instructions"),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "idempotency_key",
            name="uq_core_workflow_instructions_idempotency",
        ),
        sa.CheckConstraint(
            "status IN ('pending','applied','rejected')",
            name="ck_core_workflow_instructions_status",
        ),
        sa.CheckConstraint(
            "expected_revision > 0 AND (applied_revision IS NULL OR applied_revision > 0)",
            name="ck_core_workflow_instructions_revision",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [f"{RUNS}.tenant_id", f"{RUNS}.id"],
            name="fk_core_workflow_instructions_run",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_workflow_runs_claim",
        RUNS,
        ["tenant_id", "status", "pause_requested", "updated_at"],
    )
    op.create_index(
        "ix_core_workflow_nodes_claim",
        NODES,
        ["tenant_id", "status", "available_at", "run_id"],
    )
    op.create_index(
        "ix_core_workflow_attempts_lease",
        ATTEMPTS,
        ["tenant_id", "status", "lease_until"],
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
    op.drop_index("ix_core_workflow_attempts_lease", table_name=ATTEMPTS)
    op.drop_index("ix_core_workflow_nodes_claim", table_name=NODES)
    op.drop_index("ix_core_workflow_runs_claim", table_name=RUNS)
    for table in reversed(TABLES):
        op.drop_table(table)
