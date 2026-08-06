from __future__ import annotations

from pathlib import Path

from fairy_core.storage.sqlalchemy import SqlAlchemyStateStore
from fairy_core.storage.sqlite_engine import create_sqlite_engine
from fairy_core.storage.sqlite_evidence_migration import migrate_assistant_evidence
from fairy_core.storage.sqlite_migrations import (
    migrate_checkpoint_evidence,
    migrate_mcp_request_results,
    migrate_pre_tenant_schema,
    migrate_runtime_graph,
    migrate_runtime_workspace_binding,
    migrate_task_snapshot_binding,
)


class SqliteStateStore(SqlAlchemyStateStore):
    def __init__(self, path: Path) -> None:
        engine = create_sqlite_engine(path)
        super().__init__(
            engine,
            tenant_id="local",
            initialize_schema=True,
            owns_engine=True,
        )
        migrate_task_snapshot_binding(engine)
        migrate_assistant_evidence(engine)
        migrate_checkpoint_evidence(engine)
        migrate_mcp_request_results(engine)
        migrate_runtime_workspace_binding(engine)
        migrate_runtime_graph(engine)
        migrate_pre_tenant_schema(engine, tenant_id="local")
