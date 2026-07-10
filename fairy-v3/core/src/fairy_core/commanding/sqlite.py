from __future__ import annotations

from pathlib import Path

from fairy_core.commanding.schema import command_metadata
from fairy_core.commanding.sqlalchemy import SqlAlchemyCommandLedger
from fairy_core.commanding.sqlite_migrations import (
    migrate_pre_tenant_ledger,
    prepare_pre_tenant_schema,
)
from fairy_core.storage.sqlite_engine import create_sqlite_engine


class SqliteCommandLedger(SqlAlchemyCommandLedger):
    def __init__(self, path: Path) -> None:
        engine = create_sqlite_engine(path)
        prepare_pre_tenant_schema(engine)
        command_metadata.create_all(engine)
        migrate_pre_tenant_ledger(engine, tenant_id="local")
        super().__init__(engine, tenant_id="local", owns_engine=True)
