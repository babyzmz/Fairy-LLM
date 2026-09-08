from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
)


def build_planning_schema(
    *,
    metadata: MetaData,
    tasks: Table,
    workspaces: Table,
    versions: Table,
) -> tuple[Table, Table]:
    plans = Table(
        "core_execution_plans",
        metadata,
        Column("tenant_id", String(128), nullable=False),
        Column("id", String(36), nullable=False),
        Column("task_id", String(36), nullable=False),
        Column("workspace_id", String(36), nullable=False),
        Column("version_id", String(36), nullable=False),
        Column("manifest", JSON, nullable=False),
        Column("generation", BigInteger, nullable=False, server_default="1"),
        Column("workflow_run_id", String(36)),
        Column("workflow_plan_revision", BigInteger),
        Column("status", String(32), nullable=False),
        Column("max_model_calls", BigInteger, nullable=False),
        Column("max_tool_calls", BigInteger, nullable=False),
        Column("max_repairs", BigInteger, nullable=False),
        Column("max_duration_seconds", BigInteger, nullable=False),
        Column("model_calls_used", BigInteger, nullable=False),
        Column("tool_calls_used", BigInteger, nullable=False),
        Column("repairs_used", BigInteger, nullable=False),
        Column("revision", BigInteger, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_execution_plans"),
        UniqueConstraint(
            "tenant_id",
            "task_id",
            "generation",
            name="uq_core_execution_plans_generation",
        ),
        UniqueConstraint(
            "tenant_id",
            "workflow_run_id",
            "workflow_plan_revision",
            name="uq_core_execution_plans_workflow_revision",
        ),
        CheckConstraint("generation > 0", name="ck_core_execution_plans_generation"),
        CheckConstraint(
            "(workflow_run_id IS NULL AND workflow_plan_revision IS NULL) OR "
            "(workflow_run_id IS NOT NULL AND workflow_plan_revision IS NOT NULL "
            "AND workflow_plan_revision > 0)",
            name="ck_core_execution_plans_workflow_binding",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workflow_run_id", "workflow_plan_revision"],
            [
                "core_workflow_plan_revisions.tenant_id",
                "core_workflow_plan_revisions.run_id",
                "core_workflow_plan_revisions.revision",
            ],
            name="fk_core_execution_plans_workflow_revision",
        ),
        CheckConstraint("revision >= 0", name="ck_core_execution_plans_revision"),
        CheckConstraint(
            "max_model_calls > 0 AND max_tool_calls > 0 AND max_repairs >= 0",
            name="ck_core_execution_plans_budgets",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            [tasks.c.tenant_id, tasks.c.id],
            name="fk_core_execution_plans_task",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            [workspaces.c.tenant_id, workspaces.c.id],
            name="fk_core_execution_plans_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            [versions.c.tenant_id, versions.c.id],
            name="fk_core_execution_plans_version",
            ondelete="CASCADE",
        ),
    )
    steps = Table(
        "core_task_steps",
        metadata,
        Column("tenant_id", String(128), nullable=False),
        Column("id", String(36), nullable=False),
        Column("plan_id", String(36), nullable=False),
        Column("task_id", String(36), nullable=False),
        Column("sequence", BigInteger, nullable=False),
        Column("kind", String(32), nullable=False),
        Column("title", String(200), nullable=False),
        Column("status", String(32), nullable=False),
        Column("attempts", BigInteger, nullable=False),
        Column("error_code", String(128)),
        Column("started_at", DateTime(timezone=True)),
        Column("completed_at", DateTime(timezone=True)),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_task_steps"),
        UniqueConstraint("tenant_id", "plan_id", "sequence", name="uq_core_task_steps_sequence"),
        CheckConstraint("sequence > 0", name="ck_core_task_steps_sequence"),
        CheckConstraint("attempts >= 0", name="ck_core_task_steps_attempts"),
        ForeignKeyConstraint(
            ["tenant_id", "plan_id"],
            [plans.c.tenant_id, plans.c.id],
            name="fk_core_task_steps_plan",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            [tasks.c.tenant_id, tasks.c.id],
            name="fk_core_task_steps_task",
            ondelete="CASCADE",
        ),
    )
    Index("ix_core_execution_plans_workspace", plans.c.tenant_id, plans.c.workspace_id)
    Index("ix_core_task_steps_task", steps.c.tenant_id, steps.c.task_id, steps.c.sequence)
    return plans, steps


__all__ = ["build_planning_schema"]
