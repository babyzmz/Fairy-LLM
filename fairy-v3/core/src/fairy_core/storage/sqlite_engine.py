from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine


def create_sqlite_engine(path: Path) -> Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite+pysqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    from fairy_core.diagnostics import attach_sql_counters

    attach_sql_counters(engine)

    @event.listens_for(engine, "connect")
    def configure_connection(
        database_connection: sqlite3.Connection,
        _connection_record: object,
    ) -> None:
        cursor = database_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA busy_timeout = 5000")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.close()

    return engine
