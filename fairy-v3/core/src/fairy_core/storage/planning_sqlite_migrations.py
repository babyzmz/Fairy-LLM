from __future__ import annotations

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import BigInteger, Column, String, inspect
from sqlalchemy.engine import Engine

from fairy_core.storage.sqlite_migrations import _sqlite_rebuild_transaction


def migrate_execution_plan_generations(engine: Engine) -> None:
    """Keep historical file plans while replacing the one-plan-per-task constraint."""
    with engine.connect() as connection:
        columns = {item["name"] for item in inspect(connection).get_columns("core_execution_plans")}
    added = {"generation", "workflow_run_id", "workflow_plan_revision"}
    if added <= columns:
        return
    if added & columns:
        raise RuntimeError("Execution Plan migration has an incomplete revision binding")
    with _sqlite_rebuild_transaction(engine) as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("core_execution_plans", recreate="always") as batch:
            batch.drop_constraint("uq_core_execution_plans_task", type_="unique")
            batch.add_column(Column("generation", BigInteger, nullable=False, server_default="1"))
            batch.add_column(Column("workflow_run_id", String(36)))
            batch.add_column(Column("workflow_plan_revision", BigInteger))
            batch.create_unique_constraint(
                "uq_core_execution_plans_generation",
                ["tenant_id", "task_id", "generation"],
            )
            batch.create_unique_constraint(
                "uq_core_execution_plans_workflow_revision",
                ["tenant_id", "workflow_run_id", "workflow_plan_revision"],
            )
            batch.create_check_constraint("ck_core_execution_plans_generation", "generation > 0")
            batch.create_check_constraint(
                "ck_core_execution_plans_workflow_binding",
                "(workflow_run_id IS NULL AND workflow_plan_revision IS NULL) OR "
                "(workflow_run_id IS NOT NULL AND workflow_plan_revision IS NOT NULL "
                "AND workflow_plan_revision > 0)",
            )
            batch.create_foreign_key(
                "fk_core_execution_plans_workflow_revision",
                "core_workflow_plan_revisions",
                ["tenant_id", "workflow_run_id", "workflow_plan_revision"],
                ["tenant_id", "run_id", "revision"],
            )
