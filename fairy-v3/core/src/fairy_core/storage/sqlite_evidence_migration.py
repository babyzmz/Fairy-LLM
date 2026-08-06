from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

_ASSISTANT_EVIDENCE_REVISION = "20260806_assistant_evidence"


def migrate_assistant_evidence(engine: Engine) -> None:
    """Add durable evidence receipts and final citation bindings."""

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
            {"revision": _ASSISTANT_EVIDENCE_REVISION},
        ).first():
            return
        tables = set(inspect(connection).get_table_names())
        additions = {
            "core_assistant_turns": {
                "cited_evidence_receipt_ids": "JSON NOT NULL DEFAULT '[]'",
            },
            "core_assistant_tool_invocations": {
                "evidence_receipts": "JSON NOT NULL DEFAULT '[]'",
            },
        }
        for table_name, columns_to_add in additions.items():
            if table_name not in tables:
                continue
            columns = {column["name"] for column in inspect(connection).get_columns(table_name)}
            for name, definition in columns_to_add.items():
                if name not in columns:
                    connection.exec_driver_sql(
                        f'ALTER TABLE "{table_name}" ADD COLUMN "{name}" {definition}'
                    )
        connection.execute(
            text(
                """
                INSERT INTO core_local_migrations (revision, applied_at)
                VALUES (:revision, :applied_at)
                """
            ),
            {
                "revision": _ASSISTANT_EVIDENCE_REVISION,
                "applied_at": datetime.now(UTC).isoformat(),
            },
        )


__all__ = ["migrate_assistant_evidence"]
