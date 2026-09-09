from __future__ import annotations

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import BigInteger, Column, inspect

from fairy_core.storage.sqlite_migrations import _sqlite_rebuild_transaction

TABLE = "core_assistant_tool_invocations"
COLUMN = "workflow_objective_index"
CHECK = "ck_core_assistant_tool_invocations_objective"
UNIQUE_FIELDS = (("arguments", "argument_hash"), ("provider_call", "provider_call_id"))


def tool_objective_backfill_sql(*, postgres=False):
    def value(key):
        return f"n.payload ->> '{key}'" if postgres else f"json_extract(n.payload, '$.{key}')"

    def kind(key):
        return (f"json_typeof(n.payload -> '{key}')" if postgres
                else f"json_type(n.payload, '$.{key}')")

    raw = value("objective_index")
    valid_index = (
        f"{kind('objective_index')} = 'number' AND ({raw}) ~ '^(0|[1-9]|1[0-5])$'"
        if postgres else
        f"{kind('objective_index')} = 'integer' AND {raw} BETWEEN 0 AND 15"
    )
    integer = "number" if postgres else "integer"
    protocol_one = "'1'" if postgres else "1"
    valid = (
        f"{valid_index} AND {kind('objective_protocol')} = '{integer}' "
        f"AND {value('objective_protocol')} = {protocol_one} "
        f"AND {value('turn_id')} = i.turn_id"
    )
    children = (
        "json_array_elements(COALESCE(w.result -> 'nodes', '[]'::json)) child"
        if postgres else "json_each(w.result, '$.nodes') child"
    )
    child_payload = "child.value -> 'payload'" if postgres else (
        "json_extract(child.value, '$.payload')"
    )
    child_kind = "child.value ->> 'kind'" if postgres else (
        "json_extract(child.value, '$.kind')"
    )
    result_type = "w.result ->> 'type'" if postgres else "json_extract(w.result, '$.type')"
    # A crash can leave calls only in the fenced model checkpoint, before graph publication.
    # Both sources must agree if the real node has subsequently been published.
    prefix = (
        "WITH tool_nodes AS (SELECT tenant_id, run_id, plan_revision, payload "
        "FROM core_workflow_nodes WHERE kind = 'assistant.step.tool' UNION ALL "
        f"SELECT w.tenant_id, w.run_id, w.plan_revision, {child_payload} "
        f"FROM core_workflow_nodes w CROSS JOIN {children} "
        f"WHERE w.kind = 'assistant.step.model' AND {result_type} = 'tools' "
        f"AND {child_kind} = 'assistant.step.tool')"
    )
    nodes = (
        "FROM tool_nodes n WHERE n.tenant_id = i.tenant_id "
        f"AND {value('invocation_id')} = i.id"
    )
    invalid = (
        f"{prefix} SELECT i.id FROM {TABLE} i WHERE EXISTS (SELECT 1 {nodes} AND ("
        "n.run_id IS DISTINCT FROM i.workflow_run_id "
        "OR n.plan_revision IS DISTINCT FROM i.workflow_plan_revision "
        f"OR ({kind('objective_index')} IS NOT NULL AND NOT COALESCE(({valid}), FALSE)))) "
        f"OR (SELECT COUNT(DISTINCT COALESCE(CAST({raw} AS TEXT), '0')) {nodes}) > 1"
    )
    backfill = (
        f"{prefix}, bindings AS (SELECT i.tenant_id, i.id, COALESCE((SELECT MIN(CAST({raw} "
        f"AS INTEGER)) {nodes}), 0) AS objective_index FROM {TABLE} i) "
        f"UPDATE {TABLE} AS target SET {COLUMN} = b.objective_index FROM bindings b "
        "WHERE target.tenant_id = b.tenant_id AND target.id = b.id"
    )
    return invalid, backfill


def migrate_tool_objectives(engine):
    with engine.connect() as connection:
        inspector = inspect(connection)
        columns = {item["name"] for item in inspector.get_columns(TABLE)}
        if COLUMN in columns:
            constraints = {
                item["name"]: item["column_names"]
                for item in inspector.get_unique_constraints(TABLE)
            }
            if any(
                constraints.get(f"uq_core_assistant_tool_invocations_revision_{suffix}")
                != ["tenant_id", "turn_id", "workflow_plan_revision", COLUMN, field]
                for suffix, field in UNIQUE_FIELDS
            ) or CHECK not in {item["name"] for item in inspector.get_check_constraints(TABLE)}:
                raise RuntimeError("Tool Invocation has an incomplete objective migration")
            return
    with _sqlite_rebuild_transaction(engine) as connection:
        invalid, backfill = tool_objective_backfill_sql()
        if connection.exec_driver_sql(invalid).first() is not None:
            raise RuntimeError("Tool Invocation history has invalid objective provenance")
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table(TABLE, recreate="always") as batch:
            batch.add_column(Column(COLUMN, BigInteger, nullable=False, server_default="0"))
            for suffix, field in UNIQUE_FIELDS:
                name = f"uq_core_assistant_tool_invocations_revision_{suffix}"
                batch.drop_constraint(name, type_="unique")
                batch.create_unique_constraint(name, [
                    "tenant_id", "turn_id", "workflow_plan_revision", COLUMN, field,
                ])
            batch.create_check_constraint(
                CHECK, f"{COLUMN} >= 0 AND {COLUMN} < 16 AND "
                f"({COLUMN} = 0 OR workflow_run_id IS NOT NULL)",
            )
        connection.exec_driver_sql(backfill)
