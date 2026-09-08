from __future__ import annotations

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import BigInteger, Column, String, inspect
from sqlalchemy.engine import Engine

from fairy_core.storage.sqlite_migrations import _sqlite_rebuild_transaction

TABLE = "core_assistant_tool_invocations"


def tool_revision_backfill_sql(*, postgres: bool = False) -> tuple[str, str]:
    """One tenant/owner-scoped backfill contract for SQLite and PostgreSQL."""
    invocation_id = (
        "n.payload ->> 'invocation_id'"
        if postgres
        else "json_extract(n.payload, '$.invocation_id')"
    )
    nodes = (
        "FROM core_workflow_nodes n WHERE n.tenant_id = i.tenant_id "
        "AND n.run_id = r.id AND n.kind = 'assistant.step.tool' "
        f"AND {invocation_id} = i.id"
    )
    owners = (
        "FROM core_assistant_tool_invocations i "
        "JOIN core_assistant_turns t ON t.tenant_id = i.tenant_id AND t.id = i.turn_id "
        "LEFT JOIN core_workflow_runs r ON r.tenant_id = t.tenant_id "
        "AND r.id = t.workflow_run_id AND r.owner_id = t.id "
        "AND r.owner_kind = 'assistant_turn' AND r.task_id = i.task_id "
        "AND r.conversation_id = t.conversation_id "
        "WHERE t.execution_engine_version = 4"
    )
    invalid = (
        f"SELECT i.id {owners} AND (r.id IS NULL "
        f"OR (SELECT COUNT(DISTINCT n.plan_revision) {nodes}) > 1 "
        f"OR (NOT EXISTS (SELECT 1 {nodes}) AND r.active_plan_revision > 1 "
        "AND i.status NOT IN ('created','queued','running')))"
    )
    backfill = (
        "WITH bindings AS (SELECT i.tenant_id, i.id, r.id AS run_id, "
        f"COALESCE((SELECT MIN(n.plan_revision) {nodes}), "
        "CASE WHEN i.status IN ('created','queued','running') "
        "THEN r.active_plan_revision ELSE 1 END) AS revision "
        f"{owners}) UPDATE core_assistant_tool_invocations AS target SET "
        "workflow_run_id = b.run_id, workflow_plan_revision = b.revision "
        "FROM bindings b WHERE target.tenant_id = b.tenant_id AND target.id = b.id"
    )
    return invalid, backfill


def migrate_tool_revisions(engine: Engine) -> None:
    with engine.connect() as connection:
        columns = {item["name"] for item in inspect(connection).get_columns(TABLE)}
    added = {"workflow_run_id", "workflow_plan_revision"}
    if added <= columns:
        return
    if added & columns:
        raise RuntimeError("Tool Invocation migration has an incomplete Workflow binding")
    with _sqlite_rebuild_transaction(engine) as connection:
        invalid, backfill = tool_revision_backfill_sql()
        if connection.exec_driver_sql(invalid).first() is not None:
            raise RuntimeError("Tool Invocation history has ambiguous Workflow provenance")
        connection.exec_driver_sql(
            "DROP INDEX IF EXISTS uq_core_assistant_tool_invocations_turn_provider_call"
        )
        unique_names = {c["name"] for c in inspect(connection).get_unique_constraints(TABLE)}
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table(TABLE, recreate="always") as batch:
            if "uq_core_assistant_tool_invocations_turn_provider_call" in unique_names:
                batch.drop_constraint(
                    "uq_core_assistant_tool_invocations_turn_provider_call", type_="unique",
                )
            batch.create_unique_constraint(
                "uq_core_assistant_tool_invocations_revision_provider_call",
                ["tenant_id", "turn_id", "workflow_plan_revision", "provider_call_id"],
            )
            batch.drop_constraint(
                "uq_core_assistant_tool_invocations_turn_arguments",
                type_="unique",
            )
            batch.add_column(Column("workflow_run_id", String(36)))
            batch.add_column(
                Column("workflow_plan_revision", BigInteger, nullable=False, server_default="1")
            )
            batch.create_unique_constraint(
                "uq_core_assistant_tool_invocations_revision_arguments",
                ["tenant_id", "turn_id", "workflow_plan_revision", "argument_hash"],
            )
            batch.create_check_constraint(
                "ck_core_assistant_tool_invocations_workflow_binding",
                "workflow_plan_revision > 0 AND "
                "(workflow_run_id IS NOT NULL OR workflow_plan_revision = 1)",
            )
            batch.create_foreign_key(
                "fk_core_assistant_tool_invocations_workflow_revision",
                "core_workflow_plan_revisions",
                ["tenant_id", "workflow_run_id", "workflow_plan_revision"],
                ["tenant_id", "run_id", "revision"],
            )
        connection.exec_driver_sql(backfill)
