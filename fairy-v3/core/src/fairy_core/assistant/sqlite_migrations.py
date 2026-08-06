from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

_ASSISTANT_WORKFLOW_BINDING_REVISION = "20260807_assistant_workflow_binding"


def migrate_assistant_workflow_binding(engine: Engine) -> None:
    """Bind pre-Workflow Assistant rows to the legacy engine without rewriting them."""

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS core_local_migrations (
                revision TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        if connection.execute(
            text("SELECT 1 FROM core_local_migrations WHERE revision = :revision"),
            {"revision": _ASSISTANT_WORKFLOW_BINDING_REVISION},
        ).first():
            return
        tables = set(inspect(connection).get_table_names())
        if "core_assistant_turns" in tables:
            columns = {
                column["name"] for column in inspect(connection).get_columns("core_assistant_turns")
            }
            additions = {
                "workflow_run_id": "VARCHAR(36)",
                "execution_engine_version": "INTEGER NOT NULL DEFAULT 1",
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.exec_driver_sql(
                        f'ALTER TABLE "core_assistant_turns" ADD COLUMN "{name}" {definition}'
                    )
        connection.execute(
            text(
                """
                INSERT INTO core_local_migrations (revision, applied_at)
                VALUES (:revision, :applied_at)
                """
            ),
            {
                "revision": _ASSISTANT_WORKFLOW_BINDING_REVISION,
                "applied_at": datetime.now(UTC).isoformat(),
            },
        )


__all__ = ["migrate_assistant_workflow_binding"]
