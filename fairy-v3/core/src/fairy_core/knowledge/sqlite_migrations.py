from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Engine


def migrate_knowledge_catalog_length(engine: Engine) -> None:
    """Backfill once, in the same transaction as adding the display metadata."""
    with engine.begin() as connection:
        columns = {
            column["name"] for column in inspect(connection).get_columns("core_knowledge_revisions")
        }
        if "byte_length" in columns:
            return
        # sqlite3 legacy transaction mode does not BEGIN for DDL. Make the
        # schema addition and backfill atomic, including interrupted startups.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        if "byte_length" in {
            column["name"]
            for column in inspect(connection).get_columns("core_knowledge_revisions")
        }:
            return
        connection.exec_driver_sql(
            "ALTER TABLE core_knowledge_revisions ADD COLUMN byte_length BIGINT NOT NULL DEFAULT 0"
        )
        # SQLite text length counts characters and stops at NUL. BLOB length is bytes.
        connection.exec_driver_sql(
            "UPDATE core_knowledge_revisions SET byte_length = length(CAST(content AS BLOB))"
        )
