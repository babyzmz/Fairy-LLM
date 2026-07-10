from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from fairy_core.commanding.registry import RiskLevel
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import ScopeContract


def _now() -> datetime:
    return datetime.now(UTC)


def _as_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class CommandStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    WAITING_APPROVAL = "waiting_approval"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class EventVisibility(StrEnum):
    USER = "user"
    DEVELOPER = "developer"
    INTERNAL = "internal"


_TRANSITIONS: dict[CommandStatus, frozenset[CommandStatus]] = {
    CommandStatus.CREATED: frozenset(
        {CommandStatus.QUEUED, CommandStatus.WAITING_APPROVAL, CommandStatus.CANCELLED}
    ),
    CommandStatus.QUEUED: frozenset(
        {
            CommandStatus.WAITING_APPROVAL,
            CommandStatus.RUNNING,
            CommandStatus.CANCELLED,
            CommandStatus.INTERRUPTED,
        }
    ),
    CommandStatus.WAITING_APPROVAL: frozenset(
        {
            CommandStatus.QUEUED,
            CommandStatus.RUNNING,
            CommandStatus.REJECTED,
            CommandStatus.CANCELLED,
        }
    ),
    CommandStatus.RUNNING: frozenset(
        {
            CommandStatus.SUCCEEDED,
            CommandStatus.FAILED,
            CommandStatus.INTERRUPTED,
            CommandStatus.CANCELLED,
        }
    ),
    CommandStatus.INTERRUPTED: frozenset({CommandStatus.QUEUED, CommandStatus.FAILED}),
    CommandStatus.SUCCEEDED: frozenset(),
    CommandStatus.FAILED: frozenset(),
    CommandStatus.REJECTED: frozenset(),
    CommandStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class CommandRun:
    id: UUID
    command_name: str
    actor: str
    scope_digest: str
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    input_payload: dict[str, Any]
    risk_level: RiskLevel
    status: CommandStatus
    idempotency_key: str
    lease_owner: str | None
    lease_until: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    id: UUID
    cursor: int
    run_id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    task_sequence: int
    event_type: str
    visibility: EventVisibility
    message: str
    payload: dict[str, Any]
    schema_version: int
    created_at: datetime


class SqliteCommandLedger:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._initialize()

    def _initialize(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS command_runs (
                id TEXT PRIMARY KEY,
                command_name TEXT NOT NULL,
                actor TEXT NOT NULL,
                scope_json TEXT NOT NULL,
                scope_digest TEXT NOT NULL,
                project_id TEXT,
                conversation_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                input_json TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                status TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                lease_owner TEXT,
                lease_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS command_events (
                cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL,
                project_id TEXT,
                conversation_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                version_id TEXT,
                task_sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                visibility TEXT NOT NULL,
                message TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES command_runs(id) ON DELETE CASCADE,
                UNIQUE(task_id, task_sequence)
            );

            CREATE INDEX IF NOT EXISTS idx_command_runs_status
                ON command_runs(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_command_events_task
                ON command_events(task_id, task_sequence);
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def create_run(
        self,
        *,
        command_name: str,
        actor: str,
        scope: ScopeContract,
        input_payload: dict[str, Any],
        risk_level: RiskLevel,
        idempotency_key: str,
    ) -> CommandRun:
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT * FROM command_runs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                return self._run_from_row(existing)

            run_id = new_id()
            now = _now()
            scope_payload = self._serialize_scope(scope)
            self._connection.execute(
                """
                INSERT INTO command_runs (
                    id, command_name, actor, scope_json, scope_digest,
                    project_id, conversation_id, task_id, input_json,
                    risk_level, status, idempotency_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run_id),
                    command_name,
                    actor,
                    json.dumps(scope_payload, sort_keys=True),
                    scope.scope_digest,
                    str(scope.project_id) if scope.project_id else None,
                    str(scope.conversation_id),
                    str(scope.task_id),
                    json.dumps(input_payload, sort_keys=True),
                    risk_level.value,
                    CommandStatus.CREATED.value,
                    idempotency_key,
                    _as_text(now),
                    _as_text(now),
                ),
            )
            self._append_event_locked(
                run_id=run_id,
                task_id=scope.task_id,
                event_type="command.created",
                visibility=EventVisibility.USER,
                message=f"Command created: {command_name}",
                payload={"command_name": command_name, "status": CommandStatus.CREATED.value},
            )
            row = self._connection.execute(
                "SELECT * FROM command_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
            assert row is not None
            return self._run_from_row(row)

    def get_run(self, run_id: UUID) -> CommandRun | None:
        row = self._connection.execute(
            "SELECT * FROM command_runs WHERE id = ?", (str(run_id),)
        ).fetchone()
        return self._run_from_row(row) if row is not None else None

    def transition(self, run_id: UUID, status: CommandStatus) -> CommandRun:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM command_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
            if row is None:
                raise KeyError(f"command run not found: {run_id}")
            current = CommandStatus(row["status"])
            if status not in _TRANSITIONS[current]:
                raise InvalidTransitionError(
                    f"cannot transition CommandRun from {current} to {status}"
                )
            now = _now()
            self._connection.execute(
                "UPDATE command_runs SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, _as_text(now), str(run_id)),
            )
            self._append_event_locked(
                run_id=run_id,
                task_id=UUID(row["task_id"]),
                event_type=f"command.{status.value}",
                visibility=EventVisibility.USER,
                message=f"Command {status.value}",
                payload={"status": status.value},
            )
            updated = self._connection.execute(
                "SELECT * FROM command_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
            assert updated is not None
            return self._run_from_row(updated)

    def append_event(
        self,
        *,
        run_id: UUID,
        event_type: str,
        visibility: EventVisibility,
        message: str,
        payload: dict[str, Any],
    ) -> EventEnvelope:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT task_id FROM command_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
            if row is None:
                raise KeyError(f"command run not found: {run_id}")
            return self._append_event_locked(
                run_id=run_id,
                task_id=UUID(row["task_id"]),
                event_type=event_type,
                visibility=visibility,
                message=message,
                payload=payload,
            )

    def events_after(
        self,
        *,
        cursor: int,
        allowed_visibilities: set[EventVisibility] | None = None,
    ) -> list[EventEnvelope]:
        parameters: list[Any] = [cursor]
        query = "SELECT * FROM command_events WHERE cursor > ?"
        if allowed_visibilities is not None:
            if not allowed_visibilities:
                return []
            placeholders = ",".join("?" for _ in allowed_visibilities)
            query += f" AND visibility IN ({placeholders})"
            parameters.extend(visibility.value for visibility in allowed_visibilities)
        query += " ORDER BY cursor"
        rows = self._connection.execute(query, parameters).fetchall()
        return [self._event_from_row(row) for row in rows]

    def claim_next(self, *, worker_id: str, lease_until: datetime) -> CommandRun | None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT * FROM command_runs
                    WHERE status = ?
                    ORDER BY created_at, id
                    LIMIT 1
                    """,
                    (CommandStatus.QUEUED.value,),
                ).fetchone()
                if row is None:
                    self._connection.commit()
                    return None
                now = _now()
                self._connection.execute(
                    """
                    UPDATE command_runs
                    SET status = ?, lease_owner = ?, lease_until = ?, updated_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        CommandStatus.RUNNING.value,
                        worker_id,
                        _as_text(lease_until),
                        _as_text(now),
                        row["id"],
                        CommandStatus.QUEUED.value,
                    ),
                )
                self._append_event_locked(
                    run_id=UUID(row["id"]),
                    task_id=UUID(row["task_id"]),
                    event_type="command.running",
                    visibility=EventVisibility.USER,
                    message="Command running",
                    payload={"status": CommandStatus.RUNNING.value},
                )
                updated = self._connection.execute(
                    "SELECT * FROM command_runs WHERE id = ?", (row["id"],)
                ).fetchone()
                self._connection.commit()
                assert updated is not None
                return self._run_from_row(updated)
            except Exception:
                self._connection.rollback()
                raise

    def _append_event_locked(
        self,
        *,
        run_id: UUID,
        task_id: UUID,
        event_type: str,
        visibility: EventVisibility,
        message: str,
        payload: dict[str, Any],
    ) -> EventEnvelope:
        run = self._connection.execute(
            "SELECT project_id, conversation_id, scope_json FROM command_runs WHERE id = ?",
            (str(run_id),),
        ).fetchone()
        if run is None:
            raise KeyError(f"command run not found: {run_id}")
        scope = json.loads(run["scope_json"])
        row = self._connection.execute(
            """
            SELECT COALESCE(MAX(task_sequence), 0) + 1 AS next_sequence
            FROM command_events
            WHERE task_id = ?
            """,
            (str(task_id),),
        ).fetchone()
        sequence = int(row["next_sequence"])
        event_id = new_id()
        created_at = _now()
        cursor = self._connection.execute(
            """
            INSERT INTO command_events (
                id, run_id, project_id, conversation_id, task_id, version_id,
                task_sequence, event_type, visibility, message, payload_json,
                schema_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(event_id),
                str(run_id),
                run["project_id"],
                run["conversation_id"],
                str(task_id),
                scope.get("target_version_id"),
                sequence,
                event_type,
                visibility.value,
                message,
                json.dumps(payload, sort_keys=True),
                1,
                _as_text(created_at),
            ),
        ).lastrowid
        assert cursor is not None
        return EventEnvelope(
            id=event_id,
            cursor=int(cursor),
            run_id=run_id,
            project_id=UUID(run["project_id"]) if run["project_id"] else None,
            conversation_id=UUID(run["conversation_id"]),
            task_id=task_id,
            version_id=UUID(scope["target_version_id"]) if scope.get("target_version_id") else None,
            task_sequence=sequence,
            event_type=event_type,
            visibility=visibility,
            message=message,
            payload=payload,
            schema_version=1,
            created_at=created_at,
        )

    @staticmethod
    def _serialize_scope(scope: ScopeContract) -> dict[str, Any]:
        return {
            "workspace_type": scope.workspace_type.value,
            "project_id": str(scope.project_id) if scope.project_id else None,
            "conversation_id": str(scope.conversation_id),
            "task_id": str(scope.task_id),
            "operation_mode": scope.operation_mode.value,
            "base_version_id": str(scope.base_version_id) if scope.base_version_id else None,
            "target_version_id": str(scope.target_version_id) if scope.target_version_id else None,
            "project_root": str(scope.project_root),
            "allowed_write_paths": [str(path) for path in scope.allowed_write_paths],
            "forbidden_write_paths": [str(path) for path in scope.forbidden_write_paths],
            "execution_target": scope.execution_target,
            "network_policy": scope.network_policy,
            "memory_read_scope": list(scope.memory_read_scope),
            "memory_write_scope": list(scope.memory_write_scope),
        }

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> CommandRun:
        return CommandRun(
            id=UUID(row["id"]),
            command_name=row["command_name"],
            actor=row["actor"],
            scope_digest=row["scope_digest"],
            project_id=UUID(row["project_id"]) if row["project_id"] else None,
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            input_payload=json.loads(row["input_json"]),
            risk_level=RiskLevel(row["risk_level"]),
            status=CommandStatus(row["status"]),
            idempotency_key=row["idempotency_key"],
            lease_owner=row["lease_owner"],
            lease_until=_parse_datetime(row["lease_until"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> EventEnvelope:
        return EventEnvelope(
            id=UUID(row["id"]),
            cursor=int(row["cursor"]),
            run_id=UUID(row["run_id"]),
            project_id=UUID(row["project_id"]) if row["project_id"] else None,
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            version_id=UUID(row["version_id"]) if row["version_id"] else None,
            task_sequence=int(row["task_sequence"]),
            event_type=row["event_type"],
            visibility=EventVisibility(row["visibility"]),
            message=row["message"],
            payload=json.loads(row["payload_json"]),
            schema_version=int(row["schema_version"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
