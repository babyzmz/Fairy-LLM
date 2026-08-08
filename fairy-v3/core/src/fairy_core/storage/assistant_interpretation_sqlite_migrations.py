from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

_REVISION = "20260808_assistant_request_interpretations"


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


__all__ = ["migrate_assistant_request_interpretations"]
