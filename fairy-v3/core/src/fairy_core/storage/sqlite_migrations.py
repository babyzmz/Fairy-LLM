from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from alembic.migration import MigrationContext
from alembic.operations import Operations
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
    workspaces,
)

_PRE_TENANT_REVISION = "20260710_pre_tenant_state"
_SNAPSHOT_BINDING_REVISION = "20260711_task_snapshot_binding"
_GENERIC_APPROVAL_REVISION = "20260711_generic_approval"
_CHECKPOINT_EVIDENCE_REVISION = "20260712_checkpoint_evidence"
_MCP_REQUEST_RESULTS_REVISION = "20260712_mcp_request_results"
_HISTORY_METADATA_REVISION = "20260712_history_metadata"
_WORKSPACE_IDENTITY_REVISION = "20260713_workspace_identity"
_RUNTIME_WORKSPACE_BINDING_REVISION = "20260713_runtime_workspace_binding"
_RUNTIME_GRAPH_REVISION = "20260713_runtime_graph"


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _identity(row: Mapping[str, Any]) -> dict[str, Any]:
    return dict(row)


def _project(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "workspace_id": row["id"],
        "created_at": _datetime(row["created_at"]),
        "updated_at": _datetime(row["updated_at"]),
    }


def _conversation(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "workspace_id": row.get("project_id") or row["id"],
        "created_at": _datetime(row["created_at"]),
        "updated_at": _datetime(row["updated_at"]),
    }


def _version(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "workspace_id": row.get("project_id") or row["id"],
        "created_at": _datetime(row["created_at"]),
    }


def _task(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "workspace_id": row.get("project_id") or row["conversation_id"],
        "created_at": _datetime(row["created_at"]),
        "updated_at": _datetime(row["updated_at"]),
    }


def _changeset(row: Mapping[str, Any]) -> dict[str, Any]:
    values = dict(row)
    values["workspace_id"] = row.get("project_id") or row["conversation_id"]
    values["files"] = json.loads(values.pop("files_json"))
    values["patches"] = json.loads(values.pop("patches_json"))
    values["created_at"] = _datetime(row["created_at"])
    values["updated_at"] = _datetime(row["updated_at"])
    return values


@contextmanager
def _sqlite_rebuild_transaction(engine: Engine) -> Iterator[Connection]:
    """Run SQLite table rebuilds atomically without parent-table FK drop failures."""

    with engine.connect() as connection:
        foreign_keys_enabled = bool(connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one())
        connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
        connection.commit()
        try:
            with connection.begin():
                yield connection
                violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if violations:
                    tables = sorted({str(row[0]) for row in violations})
                    raise RuntimeError(
                        "SQLite migration would violate foreign keys in: " + ", ".join(tables)
                    )
        finally:
            if connection.in_transaction():
                connection.rollback()
            connection.exec_driver_sql(
                f"PRAGMA foreign_keys = {'ON' if foreign_keys_enabled else 'OFF'}"
            )
            connection.commit()


def migrate_workspace_identity(engine: Engine) -> None:
    """Make Workspace identity durable for existing canonical SQLite databases."""

    with _sqlite_rebuild_transaction(engine) as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS core_local_migrations "
            "(revision TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        if connection.execute(
            text("SELECT 1 FROM core_local_migrations WHERE revision = :revision"),
            {"revision": _WORKSPACE_IDENTITY_REVISION},
        ).first():
            return
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        targets = {
            "core_projects": "id",
            "core_conversations": "COALESCE(project_id, id)",
            "core_versions": "COALESCE(project_id, source_conversation_id, id)",
            "core_tasks": "COALESCE(project_id, conversation_id)",
            "core_task_workspaces": "COALESCE(project_id, conversation_id)",
            "core_project_indexes": "COALESCE(project_id, version_id)",
            "core_changesets": "COALESCE(project_id, conversation_id)",
        }
        for table_name, expression in targets.items():
            if table_name not in tables:
                continue
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            if "workspace_id" not in columns:
                connection.exec_driver_sql(
                    f'ALTER TABLE "{table_name}" ADD COLUMN workspace_id VARCHAR(36)'
                )
            connection.exec_driver_sql(
                f'UPDATE "{table_name}" SET workspace_id = {expression} WHERE workspace_id IS NULL'
            )

        now = datetime.now(UTC).isoformat()
        if "core_projects" in tables:
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO core_workspaces "
                    "(tenant_id, id, active_version_id, active_preview_id, revision, "
                    "max_files, max_bytes, created_at, updated_at) "
                    "SELECT tenant_id, workspace_id, active_version_id, active_preview_id, "
                    "revision, 200, 20971520, created_at, updated_at FROM core_projects"
                )
            )
        if "core_conversations" in tables:
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO core_workspaces "
                    "(tenant_id, id, active_version_id, active_preview_id, revision, "
                    "max_files, max_bytes, created_at, updated_at) "
                    "SELECT tenant_id, workspace_id, "
                    "COALESCE(active_draft_version_id, base_version_id), active_preview_id, "
                    "revision, 200, 20971520, created_at, updated_at "
                    "FROM core_conversations"
                )
            )

        context = MigrationContext.configure(connection)
        operations = Operations(context)
        for table_name in targets:
            if table_name not in tables:
                continue
            table_inspector = inspect(connection)
            columns = {column["name"]: column for column in table_inspector.get_columns(table_name)}
            foreign_keys = {
                item.get("name") for item in table_inspector.get_foreign_keys(table_name)
            }
            workspace_fk = f"fk_{table_name}_workspace"
            recreate = columns["workspace_id"].get("nullable", True)
            recreate = recreate or workspace_fk not in foreign_keys
            if table_name in {"core_versions", "core_project_indexes", "core_changesets"}:
                recreate = recreate or not columns["project_id"].get("nullable", True)
            checks = {
                item.get("name") for item in table_inspector.get_check_constraints(table_name)
            }
            if (
                table_name == "core_task_workspaces"
                and "ck_core_task_workspaces_project_version" in checks
            ):
                recreate = True
            if not recreate:
                continue
            with operations.batch_alter_table(table_name, recreate="always") as batch:
                batch.alter_column("workspace_id", existing_type=None, nullable=False)
                if workspace_fk not in foreign_keys:
                    batch.create_foreign_key(
                        workspace_fk,
                        "core_workspaces",
                        ["tenant_id", "workspace_id"],
                        ["tenant_id", "id"],
                        ondelete=(
                            None
                            if table_name in {"core_projects", "core_conversations"}
                            else "CASCADE"
                        ),
                    )
                if table_name in {"core_versions", "core_project_indexes", "core_changesets"}:
                    batch.alter_column("project_id", existing_type=None, nullable=True)
                if (
                    table_name == "core_task_workspaces"
                    and "ck_core_task_workspaces_project_version" in checks
                ):
                    batch.drop_constraint(
                        "ck_core_task_workspaces_project_version",
                        type_="check",
                    )

        connection.execute(
            text(
                "INSERT INTO core_local_migrations (revision, applied_at) "
                "VALUES (:revision, :applied_at)"
            ),
            {"revision": _WORKSPACE_IDENTITY_REVISION, "applied_at": now},
        )


def migrate_runtime_workspace_binding(engine: Engine) -> None:
    """Bind legacy Runtime rows to the Workspace identity owned by their Task."""

    with _sqlite_rebuild_transaction(engine) as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS core_local_migrations "
            "(revision TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        if connection.execute(
            text("SELECT 1 FROM core_local_migrations WHERE revision = :revision"),
            {"revision": _RUNTIME_WORKSPACE_BINDING_REVISION},
        ).first():
            return
        tables = set(inspect(connection).get_table_names())
        operations = Operations(MigrationContext.configure(connection))
        for table_name in ("core_runtime_sessions", "core_preview_sessions"):
            if table_name not in tables:
                continue
            table_inspector = inspect(connection)
            columns = {item["name"]: item for item in table_inspector.get_columns(table_name)}
            if "workspace_id" not in columns:
                connection.exec_driver_sql(
                    f'ALTER TABLE "{table_name}" ADD COLUMN workspace_id VARCHAR(36)'
                )
            connection.exec_driver_sql(
                f'UPDATE "{table_name}" SET workspace_id = '
                "(SELECT workspace_id FROM core_tasks WHERE "
                f'core_tasks.tenant_id = "{table_name}".tenant_id AND '
                f'core_tasks.id = "{table_name}".task_id) WHERE workspace_id IS NULL'
            )
            foreign_keys = {
                item.get("name") for item in inspect(connection).get_foreign_keys(table_name)
            }
            workspace_fk = f"fk_{table_name}_workspace"
            columns = {item["name"]: item for item in inspect(connection).get_columns(table_name)}
            if (
                columns["workspace_id"].get("nullable", True)
                or columns["version_id"].get("nullable", True)
                or workspace_fk not in foreign_keys
            ):
                with operations.batch_alter_table(table_name, recreate="always") as batch:
                    batch.alter_column("workspace_id", existing_type=None, nullable=False)
                    batch.alter_column("version_id", existing_type=None, nullable=False)
                    if workspace_fk not in foreign_keys:
                        batch.create_foreign_key(
                            workspace_fk,
                            "core_workspaces",
                            ["tenant_id", "workspace_id"],
                            ["tenant_id", "id"],
                            ondelete="CASCADE",
                        )
        connection.execute(
            text(
                "INSERT INTO core_local_migrations (revision, applied_at) "
                "VALUES (:revision, :applied_at)"
            ),
            {
                "revision": _RUNTIME_WORKSPACE_BINDING_REVISION,
                "applied_at": datetime.now(UTC).isoformat(),
            },
        )


def migrate_runtime_graph(engine: Engine) -> None:
    """Backfill durable single-service graphs for pre-graph Runtime rows."""

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS core_local_migrations "
            "(revision TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        if connection.execute(
            text("SELECT 1 FROM core_local_migrations WHERE revision = :revision"),
            {"revision": _RUNTIME_GRAPH_REVISION},
        ).first():
            return
        tables = set(inspect(connection).get_table_names())
        if "core_runtime_sessions" in tables:
            columns = {
                item["name"] for item in inspect(connection).get_columns("core_runtime_sessions")
            }
            if "runtime_graph" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE core_runtime_sessions ADD COLUMN runtime_graph JSON"
                )
            rows = connection.execute(
                text(
                    "SELECT tenant_id, id, kind FROM core_runtime_sessions "
                    "WHERE runtime_graph IS NULL"
                )
            ).mappings()
            for row in rows:
                graph = {
                    "public_service_id": "app",
                    "services": [
                        {
                            "service_id": "app",
                            "adapter": ("static" if row["kind"] == "static_site" else "legacy"),
                            "cwd": ".",
                            "readiness_path": "/",
                            "depends_on": [],
                        }
                    ],
                }
                connection.execute(
                    text(
                        "UPDATE core_runtime_sessions SET runtime_graph = :graph "
                        "WHERE tenant_id = :tenant_id AND id = :id"
                    ),
                    {
                        "graph": json.dumps(graph, separators=(",", ":")),
                        "tenant_id": row["tenant_id"],
                        "id": row["id"],
                    },
                )
        connection.execute(
            text(
                "INSERT INTO core_local_migrations (revision, applied_at) "
                "VALUES (:revision, :applied_at)"
            ),
            {
                "revision": _RUNTIME_GRAPH_REVISION,
                "applied_at": datetime.now(UTC).isoformat(),
            },
        )


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
                if destination is projects:
                    workspace_statement = sqlite_insert(workspaces).values(
                        tenant_id=tenant_id,
                        id=values["workspace_id"],
                        active_version_id=values.get("active_version_id"),
                        active_preview_id=values.get("active_preview_id"),
                        revision=values.get("revision", 0),
                        max_files=200,
                        max_bytes=20 * 1024 * 1024,
                        created_at=values["created_at"],
                        updated_at=values["updated_at"],
                    )
                    connection.execute(
                        workspace_statement.on_conflict_do_nothing(
                            index_elements=[workspaces.c.tenant_id, workspaces.c.id]
                        )
                    )
                elif destination is conversations:
                    workspace_statement = sqlite_insert(workspaces).values(
                        tenant_id=tenant_id,
                        id=values["workspace_id"],
                        active_version_id=values.get("active_draft_version_id")
                        or values.get("base_version_id"),
                        active_preview_id=values.get("active_preview_id"),
                        revision=values.get("revision", 0),
                        max_files=200,
                        max_bytes=20 * 1024 * 1024,
                        created_at=values["created_at"],
                        updated_at=values["updated_at"],
                    )
                    connection.execute(
                        workspace_statement.on_conflict_do_nothing(
                            index_elements=[workspaces.c.tenant_id, workspaces.c.id]
                        )
                    )
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


def migrate_history_metadata(engine: Engine) -> None:
    """Add revision-fenced Conversation and Task history metadata."""

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS core_local_migrations "
            "(revision TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        if connection.execute(
            text("SELECT 1 FROM core_local_migrations WHERE revision = :revision"),
            {"revision": _HISTORY_METADATA_REVISION},
        ).first():
            return
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        if "core_conversations" in tables:
            columns = {column["name"] for column in inspector.get_columns("core_conversations")}
            additions = {
                "title": "VARCHAR(200) NOT NULL DEFAULT 'New conversation'",
                "pinned_at": "DATETIME",
                "deleted_at": "DATETIME",
                "revision": "BIGINT NOT NULL DEFAULT 0",
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.exec_driver_sql(
                        f"ALTER TABLE core_conversations ADD COLUMN {name} {definition}"
                    )
        if "core_tasks" in tables:
            columns = {column["name"] for column in inspector.get_columns("core_tasks")}
            additions = {
                "display_title": "VARCHAR(200) NOT NULL DEFAULT 'Task'",
                "pinned_at": "DATETIME",
                "metadata_revision": "BIGINT NOT NULL DEFAULT 0",
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.exec_driver_sql(
                        f"ALTER TABLE core_tasks ADD COLUMN {name} {definition}"
                    )
            connection.exec_driver_sql(
                "UPDATE core_tasks SET display_title = substr(user_request, 1, 200) "
                "WHERE display_title = 'Task'"
            )
        connection.execute(
            text(
                "INSERT INTO core_local_migrations (revision, applied_at) "
                "VALUES (:revision, :applied_at)"
            ),
            {
                "revision": _HISTORY_METADATA_REVISION,
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
