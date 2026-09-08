from __future__ import annotations

from datetime import UTC, datetime

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, String, inspect, text
from sqlalchemy.engine import Engine

from fairy_core.storage.sqlite_migrations import _sqlite_rebuild_transaction

_REVISION = "20260808_assistant_request_interpretations"
_WAITING_REVISION = "20260808_assistant_waiting_for_input"
_INTENT_REVISION = "20260908_assistant_execution_intent"


def migrate_assistant_execution_intent(engine: Engine) -> None:
    """Keep legacy interpretations unbound; never synthesize historical authority."""
    table_name = "core_assistant_request_interpretations"
    with engine.begin() as connection:
        if table_name not in inspect(connection).get_table_names():
            return
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS core_local_migrations "
            "(revision TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        if connection.execute(
            text("SELECT 1 FROM core_local_migrations WHERE revision = :revision"),
            {"revision": _INTENT_REVISION},
        ).first():
            return
        columns = {column["name"] for column in inspect(connection).get_columns(table_name)}
        if "execution_intent" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE core_assistant_request_interpretations "
                "ADD COLUMN execution_intent JSON"
            )
        connection.execute(
            text(
                "INSERT INTO core_local_migrations (revision, applied_at) "
                "VALUES (:revision, :applied_at)"
            ),
            {"revision": _INTENT_REVISION, "applied_at": datetime.now(UTC).isoformat()},
        )


def migrate_assistant_request_interpretations(engine: Engine) -> None:
    """Add the active interpretation pointer to pre-authority SQLite databases."""

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS core_local_migrations "
            "(revision TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        if connection.execute(
            text("SELECT 1 FROM core_local_migrations WHERE revision = :revision"),
            {"revision": _REVISION},
        ).first():
            return
        tables = set(inspect(connection).get_table_names())
        if "core_assistant_turns" in tables:
            columns = {
                column["name"] for column in inspect(connection).get_columns("core_assistant_turns")
            }
            if "active_interpretation_revision" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE core_assistant_turns "
                    "ADD COLUMN active_interpretation_revision BIGINT"
                )
        connection.execute(
            text(
                "INSERT INTO core_local_migrations (revision, applied_at) "
                "VALUES (:revision, :applied_at)"
            ),
            {"revision": _REVISION, "applied_at": datetime.now(UTC).isoformat()},
        )


def migrate_assistant_waiting_for_input(engine: Engine) -> None:
    with _sqlite_rebuild_transaction(engine) as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS core_local_migrations "
            "(revision TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        if connection.execute(
            text("SELECT 1 FROM core_local_migrations WHERE revision = :revision"),
            {"revision": _WAITING_REVISION},
        ).first():
            return
        operations = Operations(MigrationContext.configure(connection))
        tables = set(inspect(connection).get_table_names())
        interpretation_table = "core_assistant_request_interpretations"
        if interpretation_table in tables:
            columns = {
                column["name"] for column in inspect(connection).get_columns(interpretation_table)
            }
            if "idempotency_key" not in columns:
                operations.add_column(
                    interpretation_table,
                    Column("idempotency_key", String(512)),
                )
                connection.execute(
                    text(
                        "UPDATE core_assistant_request_interpretations "
                        "SET idempotency_key = 'legacy:' || revision "
                        "WHERE idempotency_key IS NULL"
                    )
                )
                with operations.batch_alter_table(
                    interpretation_table,
                    recreate="always",
                ) as batch:
                    batch.alter_column(
                        "idempotency_key",
                        existing_type=String(512),
                        nullable=False,
                    )
                    batch.create_unique_constraint(
                        "uq_core_assistant_interpretations_idempotency",
                        ["tenant_id", "turn_id", "idempotency_key"],
                    )
        _replace_status_check(
            connection,
            operations,
            table_name="core_assistant_turns",
            constraint_name="ck_core_assistant_turns_status",
            expression=(
                "status IN ('created','running','waiting_for_tool','waiting_for_input',"
                "'completed','cancelled','failed')"
            ),
        )
        _replace_status_check(
            connection,
            operations,
            table_name="core_workflow_runs",
            constraint_name="ck_core_workflow_runs_status",
            expression=(
                "status IN ('queued','running','waiting_for_approval','waiting_for_input',"
                "'paused','completed','cancelled','failed')"
            ),
        )
        _replace_status_check(
            connection,
            operations,
            table_name="core_workflow_nodes",
            constraint_name="ck_core_workflow_nodes_status",
            expression=(
                "status IN ('pending','ready','running','waiting_for_approval',"
                "'waiting_for_input','succeeded','failed','cancelled','skipped','superseded')"
            ),
        )
        connection.execute(
            text(
                "INSERT INTO core_local_migrations (revision, applied_at) "
                "VALUES (:revision, :applied_at)"
            ),
            {
                "revision": _WAITING_REVISION,
                "applied_at": datetime.now(UTC).isoformat(),
            },
        )


def _replace_status_check(
    connection,
    operations: Operations,
    *,
    table_name: str,
    constraint_name: str,
    expression: str,
) -> None:
    if table_name not in set(inspect(connection).get_table_names()):
        return
    checks = {
        item.get("name"): str(item.get("sqltext", ""))
        for item in inspect(connection).get_check_constraints(table_name)
    }
    if "waiting_for_input" in checks.get(constraint_name, ""):
        return
    with operations.batch_alter_table(table_name, recreate="always") as batch:
        if constraint_name in checks:
            batch.drop_constraint(constraint_name, type_="check")
        batch.create_check_constraint(constraint_name, expression)


__all__ = [
    "migrate_assistant_execution_intent",
    "migrate_assistant_request_interpretations",
    "migrate_assistant_waiting_for_input",
]
