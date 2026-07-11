from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
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
_SNAPSHOT_BINDING_REVISION = "20260711_task_snapshot_binding"
_GENERIC_APPROVAL_REVISION = "20260711_generic_approval"
_CHECKPOINT_EVIDENCE_REVISION = "20260712_checkpoint_evidence"
_MCP_REQUEST_RESULTS_REVISION = "20260712_mcp_request_results"


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
    values["evidence_artifact_ids"] = json.loads(values.pop("evidence_artifact_ids_json", "[]"))
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
                "applied_at": datetime.now(UTC).isoformat(),
            },
        )


def migrate_task_snapshot_binding(engine: Engine) -> None:
    """Add nullable Task Snapshot binding columns to an existing local V3 database."""

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS core_local_migrations (
                revision TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        if connection.execute(
            text("SELECT 1 FROM core_local_migrations WHERE revision = :revision"),
            {"revision": _SNAPSHOT_BINDING_REVISION},
        ).first():
            return
        tables = set(inspect(connection).get_table_names())
        if "core_tasks" in tables:
            columns = {column["name"] for column in inspect(connection).get_columns("core_tasks")}
            if "memory_snapshot_id" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE core_tasks ADD COLUMN memory_snapshot_id VARCHAR(36)"
                )
            if "memory_snapshot_hash" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE core_tasks ADD COLUMN memory_snapshot_hash VARCHAR(64)"
                )
        connection.execute(
            text(
                """
                INSERT INTO core_local_migrations (revision, applied_at)
                VALUES (:revision, :applied_at)
                """
            ),
            {
                "revision": _SNAPSHOT_BINDING_REVISION,
                "applied_at": datetime.now(UTC).isoformat(),
            },
        )


def migrate_generic_approval(engine: Engine) -> None:
    """Add durable Assistant approval bindings to an existing local V3 database."""

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS core_local_migrations (
                revision TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        if connection.execute(
            text("SELECT 1 FROM core_local_migrations WHERE revision = :revision"),
            {"revision": _GENERIC_APPROVAL_REVISION},
        ).first():
            return
        tables = set(inspect(connection).get_table_names())
        if "core_assistant_tool_invocations" in tables:
            columns = {
                column["name"]
                for column in inspect(connection).get_columns("core_assistant_tool_invocations")
            }
            if "model_round" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE core_assistant_tool_invocations "
                    "ADD COLUMN model_round BIGINT NOT NULL DEFAULT 1"
                )
            if "provider_call_id" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE core_assistant_tool_invocations "
                    "ADD COLUMN provider_call_id VARCHAR(255)"
                )
                connection.exec_driver_sql(
                    "UPDATE core_assistant_tool_invocations "
                    "SET provider_call_id = 'legacy-' || id "
                    "WHERE provider_call_id IS NULL"
                )
            if "model_content" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE core_assistant_tool_invocations ADD COLUMN model_content TEXT"
                )
                connection.exec_driver_sql(
                    "UPDATE core_assistant_tool_invocations "
                    "SET model_content = public_summary "
                    "WHERE model_content IS NULL AND public_summary IS NOT NULL"
                )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_core_assistant_tool_invocations_turn_provider_call "
                "ON core_assistant_tool_invocations "
                "(tenant_id, turn_id, provider_call_id)"
            )
        if "core_approvals" in tables:
            columns = {
                column["name"] for column in inspect(connection).get_columns("core_approvals")
            }
            if "tool_invocation_id" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE core_approvals ADD COLUMN tool_invocation_id VARCHAR(36)"
                )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_core_approvals_tenant_command_run "
                "ON core_approvals (tenant_id, command_run_id)"
            )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_core_approvals_tenant_tool_invocation "
                "ON core_approvals (tenant_id, tool_invocation_id) "
                "WHERE tool_invocation_id IS NOT NULL"
            )
        connection.execute(
            text(
                """
                INSERT INTO core_local_migrations (revision, applied_at)
                VALUES (:revision, :applied_at)
                """
            ),
            {
                "revision": _GENERIC_APPROVAL_REVISION,
                "applied_at": datetime.now(UTC).isoformat(),
            },
        )


def migrate_checkpoint_evidence(engine: Engine) -> None:
    """Add generation-bound Runtime Review evidence to existing local Checkpoints."""

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS core_local_migrations (
                revision TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        if connection.execute(
            text("SELECT 1 FROM core_local_migrations WHERE revision = :revision"),
            {"revision": _CHECKPOINT_EVIDENCE_REVISION},
        ).first():
            return
        tables = set(inspect(connection).get_table_names())
        if "core_checkpoints" in tables:
            columns = {
                column["name"] for column in inspect(connection).get_columns("core_checkpoints")
            }
            if "evidence_artifact_ids" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE core_checkpoints ADD COLUMN "
                    "evidence_artifact_ids JSON NOT NULL DEFAULT '[]'"
                )
        connection.execute(
            text(
                """
                INSERT INTO core_local_migrations (revision, applied_at)
                VALUES (:revision, :applied_at)
                """
            ),
            {
                "revision": _CHECKPOINT_EVIDENCE_REVISION,
                "applied_at": datetime.now(UTC).isoformat(),
            },
        )


def migrate_mcp_request_results(engine: Engine) -> None:
    """Preserve MCP idempotency outcomes after server deletion and interrupted discovery."""

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS core_local_migrations (
                revision TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        if connection.execute(
            text("SELECT 1 FROM core_local_migrations WHERE revision = :revision"),
            {"revision": _MCP_REQUEST_RESULTS_REVISION},
        ).first():
            return
        tables = set(inspect(connection).get_table_names())
        if "core_mcp_server_updates" in tables:
            columns = {
                column["name"]
                for column in inspect(connection).get_columns("core_mcp_server_updates")
            }
            if "result_deleted" not in columns:
                connection.exec_driver_sql(
                    """
                    CREATE TABLE core_mcp_server_updates_v2 (
                        tenant_id VARCHAR(128) NOT NULL,
                        idempotency_key VARCHAR(512) NOT NULL,
                        request_fingerprint VARCHAR(64) NOT NULL,
                        server_id VARCHAR(64) NOT NULL,
                        result_record JSON,
                        result_deleted BOOLEAN,
                        result_error_code VARCHAR(128),
                        created_at DATETIME NOT NULL,
                        CONSTRAINT pk_core_mcp_server_updates
                            PRIMARY KEY (tenant_id, idempotency_key),
                        CONSTRAINT ck_core_mcp_server_updates_fingerprint
                            CHECK (
                                length(request_fingerprint) = 64
                                AND request_fingerprint = lower(request_fingerprint)
                            ),
                        CONSTRAINT ck_core_mcp_server_updates_result CHECK (
                            (result_record IS NULL AND result_deleted IS NULL
                                AND result_error_code IS NULL)
                            OR (result_record IS NOT NULL AND result_deleted = 0
                                AND result_error_code IS NULL)
                            OR (result_record IS NULL AND result_deleted = 1
                                AND result_error_code IS NULL)
                            OR (result_record IS NULL AND result_deleted = 0
                                AND result_error_code IS NOT NULL)
                        )
                    )
                    """
                )
                connection.exec_driver_sql(
                    """
                    INSERT INTO core_mcp_server_updates_v2 (
                        tenant_id, idempotency_key, request_fingerprint, server_id,
                        result_record, result_deleted, result_error_code, created_at
                    )
                    SELECT tenant_id, idempotency_key, request_fingerprint, server_id,
                           result_record, 0, NULL, created_at
                    FROM core_mcp_server_updates
                    """
                )
                connection.exec_driver_sql("DROP TABLE core_mcp_server_updates")
                connection.exec_driver_sql(
                    "ALTER TABLE core_mcp_server_updates_v2 RENAME TO core_mcp_server_updates"
                )
        connection.execute(
            text(
                """
                INSERT INTO core_local_migrations (revision, applied_at)
                VALUES (:revision, :applied_at)
                """
            ),
            {
                "revision": _MCP_REQUEST_RESULTS_REVISION,
                "applied_at": datetime.now(UTC).isoformat(),
            },
        )
