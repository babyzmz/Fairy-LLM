from __future__ import annotations

from sqlalchemy import Index, Table


def build_runtime_scope_indexes(runtimes: Table, previews: Table) -> None:
    for table in (runtimes, previews):
        Index(
            f"ix_{table.name}_scope",
            table.c.tenant_id,
            table.c.task_id,
            table.c.workspace_id,
            table.c.version_id,
        )


__all__ = ["build_runtime_scope_indexes"]
