from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine

from fairy_core.storage.sqlalchemy import SqlAlchemyStateStore


class SqliteStateStore(SqlAlchemyStateStore):
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite+pysqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 5},
        )
        super().__init__(
            engine,
            tenant_id="local",
            initialize_schema=True,
            owns_engine=True,
        )
