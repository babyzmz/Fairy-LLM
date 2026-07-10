from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine

from fairy_core.storage.schema import (
    approvals,
    changesets,
    checkpoints,
    conversations,
    projects,
    tasks,
    versions,
)

_PRE_TENANT_REVISION = "20260710_pre_tenant_state"


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _identity(row: Mapping[str, Any]) -> dict[str, Any]:
    return dict(row)


def _project(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "created_at": _datetime(row["created_at"]),
        "updated_at": _datetime(row["updated_at"]),
    }


def _conversation(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "created_at": _datetime(row["created_at"]),
        "updated_at": _datetime(row["updated_at"]),
    }


def _version(row: Mapping[str, Any]) -> dict[str, Any]:
    return {**row, "created_at": _datetime(row["created_at"])}


def _task(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "created_at": _datetime(row["created_at"]),
        "updated_at": _datetime(row["updated_at"]),
    }


def _changeset(row: Mapping[str, Any]) -> dict[str, Any]:
    values = dict(row)
    values["files"] = json.loads(values.pop("files_json"))
    values["patches"] = json.loads(values.pop("patches_json"))
    values["created_at"] = _datetime(row["created_at"])
    values["updated_at"] = _datetime(row["updated_at"])
    return values


def _approval(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "created_at": _datetime(row["created_at"]),
        "decided_at": _datetime(row["decided_at"]),
    }


def _checkpoint(row: Mapping[str, Any]) -> dict[str, Any]:
    values = dict(row)
    values["changed_files"] = json.loads(values.pop("changed_files_json"))
    values["command_run_ids"] = json.loads(values.pop("command_run_ids_json"))
    values["created_at"] = _datetime(row["created_at"])
    return values


_TABLES: tuple[tuple[str, Any, Callable[[Mapping[str, Any]], dict[str, Any]]], ...] = (
    ("projects", projects, _project),
    ("conversations", conversations, _conversation),
    ("versions", versions, _version),
    ("tasks", tasks, _task),
    ("changesets", changesets, _changeset),
    ("approvals", approvals, _approval),
    ("checkpoints", checkpoints, _checkpoint),
)


def _migration_applied(connection: Connection) -> bool:
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS core_local_migrations (
            revision TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    return (
        connection.execute(
            text("SELECT 1 FROM core_local_migrations WHERE revision = :revision"),
            {"revision": _PRE_TENANT_REVISION},
        ).first()
        is not None
    )


def migrate_pre_tenant_schema(engine: Engine, *, tenant_id: str) -> None:
    """Import the pre-tenant V3 SQLite tables once without touching legacy Fairy DBs."""

    with engine.begin() as connection:
        if _migration_applied(connection):
            return
        existing_tables = set(inspect(connection).get_table_names())
        for source_name, destination, transform in _TABLES:
            if source_name not in existing_tables:
                continue
            rows = connection.execute(text(f'SELECT * FROM "{source_name}"')).mappings()
            for row in rows:
                values = {"tenant_id": tenant_id, **transform(row)}
                statement = sqlite_insert(destination).values(**values)
                statement = statement.on_conflict_do_nothing(
                    index_elements=[destination.c.tenant_id, destination.c.id]
                )
                connection.execute(statement)
        connection.execute(
            text(
                """
                INSERT INTO core_local_migrations (revision, applied_at)
                VALUES (:revision, :applied_at)
                """
            ),
            {
                "revision": _PRE_TENANT_REVISION,
                "applied_at": datetime.now().astimezone().isoformat(),
            },
        )
