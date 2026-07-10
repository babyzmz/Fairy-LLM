from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine

from fairy_core.commanding.models import CommandStatus, EventVisibility
from fairy_core.commanding.registry import RiskLevel
from fairy_core.commanding.schema import command_runs, domain_events, task_event_sequences
from fairy_core.commanding.sqlalchemy import command_request_fingerprint
from fairy_core.domain.ids import new_id

_LEGACY_RUNS = "legacy_command_runs_pre_tenant"
_PRE_TENANT_REVISION = "20260710_pre_tenant_ledger"


def prepare_pre_tenant_schema(engine: Engine) -> None:
    """Move the pre-tenant V3 run table aside before SQLAlchemy creates the new table."""

    with engine.begin() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        if "command_runs" not in tables or _LEGACY_RUNS in tables:
            return
        columns = {column["name"] for column in inspector.get_columns("command_runs")}
        if "tenant_id" not in columns:
            connection.exec_driver_sql(f'ALTER TABLE "command_runs" RENAME TO "{_LEGACY_RUNS}"')


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


def _load_rows(connection: Connection, table_name: str) -> list[dict[str, Any]]:
    return [
        dict(row) for row in connection.execute(text(f'SELECT * FROM "{table_name}"')).mappings()
    ]


def migrate_pre_tenant_ledger(engine: Engine, *, tenant_id: str) -> None:
    """Import only the pre-tenant Fairy V3 ledger schema into the local tenant."""

    with engine.begin() as connection:
        if _migration_applied(connection):
            return
        tables = set(inspect(connection).get_table_names())
        if _LEGACY_RUNS not in tables:
            _record_migration(connection)
            return

        legacy_runs = _load_rows(connection, _LEGACY_RUNS)
        runs_by_id: dict[str, dict[str, Any]] = {}
        interrupted_runs: list[dict[str, Any]] = []
        for row in legacy_runs:
            scope = json.loads(row["scope_json"])
            scope["scope_digest"] = row["scope_digest"]
            input_payload = json.loads(row["input_json"])
            fingerprint = command_request_fingerprint(
                command_name=row["command_name"],
                actor=row["actor"],
                scope=scope,
                input_payload=input_payload,
                risk_level=RiskLevel(row["risk_level"]),
            )
            status = CommandStatus(row["status"])
            lease_owner = row["lease_owner"]
            lease_until = datetime.fromisoformat(row["lease_until"]) if row["lease_until"] else None
            missing_worker_lease = status is CommandStatus.RUNNING and (
                not lease_owner or lease_until is None
            )
            if missing_worker_lease:
                status = CommandStatus.INTERRUPTED
                lease_owner = None
                lease_until = None
            values = {
                "tenant_id": tenant_id,
                "id": row["id"],
                "command_name": row["command_name"],
                "actor": row["actor"],
                "scope": scope,
                "scope_digest": row["scope_digest"],
                "project_id": row["project_id"],
                "conversation_id": row["conversation_id"],
                "task_id": row["task_id"],
                "input": input_payload,
                "risk_level": row["risk_level"],
                "status": status.value,
                "idempotency_key": row["idempotency_key"],
                "request_fingerprint": fingerprint,
                "lease_owner": lease_owner,
                "lease_until": lease_until,
                "lease_fence": 1 if lease_owner else 0,
                "created_at": datetime.fromisoformat(row["created_at"]),
                "updated_at": datetime.fromisoformat(row["updated_at"]),
            }
            result = connection.execute(
                sqlite_insert(command_runs)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=[command_runs.c.tenant_id, command_runs.c.id]
                )
            )
            runs_by_id[row["id"]] = values
            if missing_worker_lease and result.rowcount == 1:
                interrupted_runs.append(values)

        if "command_events" in tables:
            for row in _load_rows(connection, "command_events"):
                run = runs_by_id.get(row["run_id"])
                if run is None:
                    continue
                connection.execute(
                    sqlite_insert(domain_events)
                    .values(
                        cursor=int(row["cursor"]),
                        tenant_id=tenant_id,
                        event_id=row["id"],
                        run_id=row["run_id"],
                        user_id=run["actor"],
                        device_id="core",
                        project_id=row["project_id"],
                        conversation_id=row["conversation_id"],
                        task_id=row["task_id"],
                        version_id=row["version_id"],
                        task_sequence=int(row["task_sequence"]),
                        schema_version=int(row["schema_version"]),
                        event_type=row["event_type"],
                        visibility=row["visibility"],
                        message=row["message"],
                        payload=json.loads(row["payload_json"]),
                        created_at=datetime.fromisoformat(row["created_at"]),
                    )
                    .on_conflict_do_nothing(
                        index_elements=[domain_events.c.tenant_id, domain_events.c.event_id]
                    )
                )
            sequences = connection.execute(
                text(
                    """
                    SELECT task_id, MAX(task_sequence) AS last_sequence
                    FROM command_events
                    GROUP BY task_id
                    """
                )
            ).mappings()
            for row in sequences:
                connection.execute(
                    sqlite_insert(task_event_sequences)
                    .values(
                        tenant_id=tenant_id,
                        task_id=row["task_id"],
                        last_sequence=int(row["last_sequence"]),
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            task_event_sequences.c.tenant_id,
                            task_event_sequences.c.task_id,
                        ],
                        set_={"last_sequence": int(row["last_sequence"])},
                    )
                )
        for run in interrupted_runs:
            _append_interrupted_migration_event(connection, tenant_id=tenant_id, run=run)
        _record_migration(connection)


def _append_interrupted_migration_event(
    connection: Connection,
    *,
    tenant_id: str,
    run: dict[str, Any],
) -> None:
    sequence_statement = (
        sqlite_insert(task_event_sequences)
        .values(
            tenant_id=tenant_id,
            task_id=run["task_id"],
            last_sequence=1,
        )
        .on_conflict_do_update(
            index_elements=[
                task_event_sequences.c.tenant_id,
                task_event_sequences.c.task_id,
            ],
            set_={"last_sequence": task_event_sequences.c.last_sequence + 1},
        )
    )
    sequence = int(
        connection.execute(
            sequence_statement.returning(task_event_sequences.c.last_sequence)
        ).scalar_one()
    )
    connection.execute(
        sqlite_insert(domain_events).values(
            tenant_id=tenant_id,
            event_id=str(new_id()),
            run_id=run["id"],
            user_id=run["actor"],
            device_id="core-migration",
            project_id=run["project_id"],
            conversation_id=run["conversation_id"],
            task_id=run["task_id"],
            version_id=run["scope"].get("target_version_id"),
            task_sequence=sequence,
            schema_version=1,
            event_type="command.interrupted",
            visibility=EventVisibility.USER.value,
            message="Command interrupted during local database migration",
            payload={
                "reason": "missing_worker_lease",
                "status": CommandStatus.INTERRUPTED.value,
            },
            created_at=datetime.now(UTC),
        )
    )


def _record_migration(connection: Connection) -> None:
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
