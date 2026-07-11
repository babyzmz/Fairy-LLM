from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError

_FTS_TABLE = "core_document_chunks_fts"
_TRIGGERS = (
    "core_document_chunks_fts_ai",
    "core_document_chunks_fts_ad",
    "core_document_chunks_fts_au",
)


def initialize_document_sqlite_fts(engine: Engine) -> bool:
    if engine.dialect.name != "sqlite":
        raise ValueError("Document FTS initialization requires a SQLite engine")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS core_document_chunks_fts
                USING fts5(
                    normalized_text,
                    content='core_document_chunks',
                    content_rowid='fts_rowid',
                    tokenize='unicode61 remove_diacritics 2'
                )
                """
            )
            connection.exec_driver_sql(
                """
                CREATE TRIGGER IF NOT EXISTS core_document_chunks_fts_ai
                AFTER INSERT ON core_document_chunks BEGIN
                    INSERT INTO core_document_chunks_fts(rowid, normalized_text)
                    VALUES (new.fts_rowid, new.normalized_text);
                END
                """
            )
            connection.exec_driver_sql(
                """
                CREATE TRIGGER IF NOT EXISTS core_document_chunks_fts_ad
                AFTER DELETE ON core_document_chunks BEGIN
                    INSERT INTO core_document_chunks_fts(
                        core_document_chunks_fts,
                        rowid,
                        normalized_text
                    ) VALUES ('delete', old.fts_rowid, old.normalized_text);
                END
                """
            )
            connection.exec_driver_sql(
                """
                CREATE TRIGGER IF NOT EXISTS core_document_chunks_fts_au
                AFTER UPDATE ON core_document_chunks BEGIN
                    INSERT INTO core_document_chunks_fts(
                        core_document_chunks_fts,
                        rowid,
                        normalized_text
                    ) VALUES ('delete', old.fts_rowid, old.normalized_text);
                    INSERT INTO core_document_chunks_fts(rowid, normalized_text)
                    VALUES (new.fts_rowid, new.normalized_text);
                END
                """
            )
            connection.exec_driver_sql(
                "INSERT INTO core_document_chunks_fts(core_document_chunks_fts) VALUES ('rebuild')"
            )
    except OperationalError as error:
        if "no such module: fts5" in str(error).casefold():
            return False
        raise
    return True


def document_sqlite_fts_available(connection: Connection) -> bool:
    if connection.dialect.name != "sqlite":
        return False
    return (
        connection.execute(
            text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :name"),
            {"name": _FTS_TABLE},
        ).first()
        is not None
    )


__all__ = ["document_sqlite_fts_available", "initialize_document_sqlite_fts"]
