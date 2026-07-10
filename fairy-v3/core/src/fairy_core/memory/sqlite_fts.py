from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError

_FTS_TABLE = "memory_search_documents_fts"
_TRIGGERS = (
    "memory_search_documents_fts_ai",
    "memory_search_documents_fts_ad",
    "memory_search_documents_fts_au",
)


def initialize_sqlite_fts(engine: Engine) -> bool:
    """Create the local external-content FTS projection when FTS5 is available."""

    if engine.dialect.name != "sqlite":
        raise ValueError("SQLite FTS initialization requires a SQLite engine")
    try:
        with engine.begin() as connection:
            existing_sql = connection.execute(
                text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :table_name"),
                {"table_name": _FTS_TABLE},
            ).scalar_one_or_none()
            existing_triggers = {
                str(row[0])
                for row in connection.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'trigger' AND name IN "
                        "('memory_search_documents_fts_ai', "
                        "'memory_search_documents_fts_ad', "
                        "'memory_search_documents_fts_au')"
                    )
                )
            }
            if existing_sql is not None and "content_rowid='fts_rowid'" not in existing_sql:
                for trigger_name in _TRIGGERS:
                    connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{trigger_name}"')
                connection.exec_driver_sql("DROP TABLE IF EXISTS memory_search_documents_fts")
                existing_sql = None
                existing_triggers = set()
            _ensure_stable_row_identity(connection)
            connection.exec_driver_sql(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_search_documents_fts
                USING fts5(
                    normalized_text,
                    content='memory_search_documents',
                    content_rowid='fts_rowid',
                    tokenize='unicode61 remove_diacritics 2'
                )
                """
            )
            connection.exec_driver_sql(
                """
                CREATE TRIGGER IF NOT EXISTS memory_search_documents_fts_ai
                AFTER INSERT ON memory_search_documents BEGIN
                    INSERT INTO memory_search_documents_fts(rowid, normalized_text)
                    VALUES (new.fts_rowid, new.normalized_text);
                END
                """
            )
            connection.exec_driver_sql(
                """
                CREATE TRIGGER IF NOT EXISTS memory_search_documents_fts_ad
                AFTER DELETE ON memory_search_documents BEGIN
                    INSERT INTO memory_search_documents_fts(
                        memory_search_documents_fts,
                        rowid,
                        normalized_text
                    ) VALUES ('delete', old.fts_rowid, old.normalized_text);
                END
                """
            )
            connection.exec_driver_sql(
                """
                CREATE TRIGGER IF NOT EXISTS memory_search_documents_fts_au
                AFTER UPDATE ON memory_search_documents BEGIN
                    INSERT INTO memory_search_documents_fts(
                        memory_search_documents_fts,
                        rowid,
                        normalized_text
                    ) VALUES ('delete', old.fts_rowid, old.normalized_text);
                    INSERT INTO memory_search_documents_fts(rowid, normalized_text)
                    VALUES (new.fts_rowid, new.normalized_text);
                END
                """
            )
            if existing_sql is None or existing_triggers != set(_TRIGGERS):
                connection.exec_driver_sql(
                    "INSERT INTO memory_search_documents_fts("
                    "memory_search_documents_fts) VALUES ('rebuild')"
                )
    except OperationalError as error:
        if "no such module: fts5" in str(error).casefold():
            return False
        raise
    return True


def _ensure_stable_row_identity(connection: Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.exec_driver_sql("PRAGMA table_info(memory_search_documents)")
    }
    if "fts_rowid" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE memory_search_documents ADD COLUMN fts_rowid BIGINT"
        )
    connection.exec_driver_sql(
        "UPDATE memory_search_documents SET fts_rowid = rowid WHERE fts_rowid IS NULL"
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_search_documents_fts_rowid "
        "ON memory_search_documents (fts_rowid)"
    )


def sqlite_fts_available(connection: Connection) -> bool:
    if connection.dialect.name != "sqlite":
        return False
    return (
        connection.execute(
            text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :table_name"),
            {"table_name": _FTS_TABLE},
        ).first()
        is not None
    )


__all__ = ["initialize_sqlite_fts", "sqlite_fts_available"]
