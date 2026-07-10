from __future__ import annotations

from pathlib import Path

from fairy_core.storage.sqlalchemy import SqlAlchemyStateStore
from fairy_core.storage.sqlite_engine import create_sqlite_engine
from fairy_core.storage.sqlite_migrations import migrate_pre_tenant_schema


class SqliteStateStore(SqlAlchemyStateStore):
    def __init__(self, path: Path) -> None:
        engine = create_sqlite_engine(path)
        super().__init__(
            engine,
            tenant_id="local",
            initialize_schema=True,
            owns_engine=True,
        )
        migrate_pre_tenant_schema(engine, tenant_id="local")
