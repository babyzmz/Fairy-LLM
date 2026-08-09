from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

_REVISION = "20260807_assistant_schedule_operation_mode"
_INTERPRETATION_REVISION = "20260809_assistant_schedule_interpretation"


def migrate_assistant_schedule_operation_mode(engine: Engine) -> None:
    """Bind schedules from earlier development builds to Answer mode."""

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
        if "core_assistant_schedules" in tables:
            columns = {
                column["name"]
                for column in inspect(connection).get_columns("core_assistant_schedules")
            }
            if "operation_mode" not in columns:
                connection.exec_driver_sql(
                    'ALTER TABLE "core_assistant_schedules" ADD COLUMN '
                    "\"operation_mode\" VARCHAR(32) NOT NULL DEFAULT 'answer'"
                )
        if "core_assistant_schedule_occurrences" in tables:
            occurrence_columns = {
                column["name"]
                for column in inspect(connection).get_columns("core_assistant_schedule_occurrences")
            }
            if "idempotency_key" not in occurrence_columns:
                connection.exec_driver_sql(
                    'ALTER TABLE "core_assistant_schedule_occurrences" ADD COLUMN '
                    '"idempotency_key" VARCHAR(512)'
                )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_core_assistant_occurrences_idempotency ON "
                "core_assistant_schedule_occurrences "
                "(tenant_id, schedule_id, idempotency_key) "
                "WHERE idempotency_key IS NOT NULL"
            )
        connection.execute(
            text(
                "INSERT INTO core_local_migrations (revision, applied_at) "
                "VALUES (:revision, :applied_at)"
            ),
            {"revision": _REVISION, "applied_at": datetime.now(UTC).isoformat()},
        )


def migrate_assistant_schedule_interpretation(engine: Engine) -> None:
    """Add the bounded authoring interpretation to existing local schedules."""

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS core_local_migrations "
            "(revision TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        if connection.execute(
            text("SELECT 1 FROM core_local_migrations WHERE revision = :revision"),
            {"revision": _INTERPRETATION_REVISION},
        ).first():
            return
        tables = set(inspect(connection).get_table_names())
        if "core_assistant_schedules" in tables:
            columns = {
                column["name"]
                for column in inspect(connection).get_columns("core_assistant_schedules")
            }
            additions = {
                "interpretation_action": "VARCHAR(32)",
                "interpretation_summary": "VARCHAR(240)",
                "instruction_sha256": "VARCHAR(64)",
            }
            for name, kind in additions.items():
                if name not in columns:
                    connection.exec_driver_sql(
                        f'ALTER TABLE "core_assistant_schedules" ADD COLUMN "{name}" {kind}'
                    )
        connection.execute(
            text(
                "INSERT INTO core_local_migrations (revision, applied_at) "
                "VALUES (:revision, :applied_at)"
            ),
            {
                "revision": _INTERPRETATION_REVISION,
                "applied_at": datetime.now(UTC).isoformat(),
            },
        )


__all__ = [
    "migrate_assistant_schedule_interpretation",
    "migrate_assistant_schedule_operation_mode",
]
