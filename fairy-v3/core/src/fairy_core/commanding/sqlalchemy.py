from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine, RowMapping

from fairy_core.commanding.models import (
    COMMAND_TRANSITIONS,
    CommandRun,
    CommandStatus,
    EventEnvelope,
    EventVisibility,
)
from fairy_core.commanding.registry import RiskLevel
from fairy_core.commanding.schema import (
    command_metadata,
    command_runs,
    domain_events,
    task_event_sequences,
)
from fairy_core.domain.errors import (
    IdempotencyConflictError,
    InvalidTransitionError,
    WorkerFenceError,
)
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import ScopeContract
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.persistence.tenant import normalize_tenant_id


def _now() -> datetime:
    return datetime.now(UTC)


def _datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def command_request_fingerprint(
    *,
    command_name: str,
    actor: str,
    scope: Mapping[str, Any],
    input_payload: Mapping[str, Any],
    risk_level: RiskLevel,
) -> str:
    canonical = json.dumps(
        {
            "command_name": command_name,
            "actor": actor,
            "scope": dict(scope),
            "input": dict(input_payload),
            "risk_level": risk_level.value,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SqlAlchemyCommandLedger:
    """Tenant-scoped command and event ledger for SQLite and PostgreSQL."""

    def __init__(
        self,
        bind: Engine | Connection,
        *,
        tenant_id: str,
        initialize_schema: bool = False,
        owns_engine: bool = False,
    ) -> None:
        normalized_tenant = normalize_tenant_id(tenant_id)
        dialect_name = bind.dialect.name
        if dialect_name not in {"postgresql", "sqlite"}:
            raise ValueError(f"unsupported command-ledger dialect: {dialect_name}")
        if initialize_schema and dialect_name != "sqlite":
            raise ValueError("PostgreSQL schemas must be initialized through Alembic")
        self._tenant_id = normalized_tenant
        self._session = SqlAlchemySession(bind, owns_engine=owns_engine)
        if initialize_schema:
            command_metadata.create_all(bind)

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def close(self) -> None:
        self._session.close()

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
        scope_payload = self._serialize_scope(scope)
        fingerprint = command_request_fingerprint(
            command_name=command_name,
            actor=actor,
            scope=scope_payload,
            input_payload=input_payload,
            risk_level=risk_level,
        )
        run_id = new_id()
        now = _now()
        values = {
            "tenant_id": self._tenant_id,
            "id": str(run_id),
            "command_name": command_name,
            "actor": actor,
            "scope": scope_payload,
            "scope_digest": scope.scope_digest,
            "project_id": str(scope.project_id) if scope.project_id else None,
            "conversation_id": str(scope.conversation_id),
            "task_id": str(scope.task_id),
            "input": dict(input_payload),
            "risk_level": risk_level.value,
            "status": CommandStatus.CREATED.value,
            "idempotency_key": idempotency_key,
            "request_fingerprint": fingerprint,
            "lease_fence": 0,
            "created_at": now,
            "updated_at": now,
        }
        statement = self._insert(command_runs).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=[command_runs.c.tenant_id, command_runs.c.idempotency_key]
        )
        with self._session.write() as connection:
            inserted_id = connection.execute(
                statement.returning(command_runs.c.id)
            ).scalar_one_or_none()
            row = self._run_by_idempotency_key(connection, idempotency_key)
            assert row is not None
            if row["request_fingerprint"] != fingerprint:
                raise IdempotencyConflictError(
                    "idempotency key was already used for a different command request"
                )
            if inserted_id is not None:
                self._append_event(
                    connection,
                    run=row,
                    event_type="command.created",
                    visibility=EventVisibility.USER,
                    message=f"Command created: {command_name}",
                    payload={
                        "command_name": command_name,
                        "status": CommandStatus.CREATED.value,
                    },
                )
        return self._run_from_row(row)

    def get_run(self, run_id: UUID) -> CommandRun | None:
        with self._session.read() as connection:
            row = self._run_by_id(connection, run_id)
        return self._run_from_row(row) if row is not None else None

    def transition(
        self,
        run_id: UUID,
        status: CommandStatus,
        *,
        lease_owner: str | None = None,
        lease_fence: int | None = None,
    ) -> CommandRun:
        if status is CommandStatus.RUNNING:
            raise InvalidTransitionError("use claim() to enter the running state")
        now = _now()
        with self._session.write() as connection:
            row = self._run_by_id(connection, run_id, for_update=True)
            if row is None:
                raise KeyError(f"command run not found: {run_id}")
            current = CommandStatus(row["status"])
            if status not in COMMAND_TRANSITIONS[current]:
                raise InvalidTransitionError(
                    f"cannot transition CommandRun from {current} to {status}"
                )
            lease_predicates = self._lease_predicates(
                row,
                lease_owner=lease_owner,
                lease_fence=lease_fence,
                now=now,
            )
            result = connection.execute(
                update(command_runs)
                .where(
                    command_runs.c.tenant_id == self._tenant_id,
                    command_runs.c.id == str(run_id),
                    command_runs.c.status == current.value,
                    *lease_predicates,
                )
                .values(status=status.value, updated_at=now)
            )
            if result.rowcount != 1:
                if row["lease_owner"] is not None:
                    raise WorkerFenceError("worker lease changed or expired")
                raise InvalidTransitionError("command status changed concurrently")
            updated = self._run_by_id(connection, run_id)
            assert updated is not None
            self._append_event(
                connection,
                run=updated,
                event_type=f"command.{status.value}",
                visibility=EventVisibility.USER,
                message=f"Command {status.value}",
                payload={"status": status.value},
            )
        return self._run_from_row(updated)

    def append_event(
        self,
        *,
        run_id: UUID,
        event_type: str,
        visibility: EventVisibility,
        message: str,
        payload: dict[str, Any],
        lease_owner: str | None = None,
        lease_fence: int | None = None,
    ) -> EventEnvelope:
        with self._session.write() as connection:
            run = self._run_by_id(connection, run_id, for_update=True)
            if run is None:
                raise KeyError(f"command run not found: {run_id}")
            lease_predicates = self._lease_predicates(
                run,
                lease_owner=lease_owner,
                lease_fence=lease_fence,
                now=_now(),
            )
            if lease_predicates:
                result = connection.execute(
                    update(command_runs)
                    .where(
                        command_runs.c.tenant_id == self._tenant_id,
                        command_runs.c.id == str(run_id),
                        command_runs.c.status == CommandStatus.RUNNING.value,
                        *lease_predicates,
                    )
                    .values(lease_fence=lease_fence)
                )
                if result.rowcount != 1:
                    raise WorkerFenceError("worker lease changed or expired")
                run = self._run_by_id(connection, run_id)
                assert run is not None
            return self._append_event(
                connection,
                run=run,
                event_type=event_type,
                visibility=visibility,
                message=message,
                payload=payload,
            )

    def finish(
        self,
        run_id: UUID,
        *,
        status: CommandStatus,
        event_type: str,
        visibility: EventVisibility,
        message: str,
        payload: dict[str, Any],
        lease_owner: str | None = None,
        lease_fence: int | None = None,
    ) -> CommandRun:
        if status not in {CommandStatus.SUCCEEDED, CommandStatus.FAILED}:
            raise ValueError("finish status must be succeeded or failed")
        now = _now()
        with self._session.write() as connection:
            row = self._run_by_id(connection, run_id, for_update=True)
            if row is None:
                raise KeyError(f"command run not found: {run_id}")
            current = CommandStatus(row["status"])
            if status not in COMMAND_TRANSITIONS[current]:
                raise InvalidTransitionError(
                    f"cannot transition CommandRun from {current} to {status}"
                )
            lease_predicates = self._lease_predicates(
                row,
                lease_owner=lease_owner,
                lease_fence=lease_fence,
                now=now,
            )
            result = connection.execute(
                update(command_runs)
                .where(
                    command_runs.c.tenant_id == self._tenant_id,
                    command_runs.c.id == str(run_id),
                    command_runs.c.status == current.value,
                    *lease_predicates,
                )
                .values(status=status.value, updated_at=now)
            )
            if result.rowcount != 1:
                if row["lease_owner"] is not None:
                    raise WorkerFenceError("worker lease changed or expired")
                raise InvalidTransitionError("command status changed concurrently")
            updated = self._run_by_id(connection, run_id)
            assert updated is not None
            self._append_event(
                connection,
                run=updated,
                event_type=event_type,
                visibility=visibility,
                message=message,
                payload=payload,
            )
            self._append_event(
                connection,
                run=updated,
                event_type=f"command.{status.value}",
                visibility=EventVisibility.USER,
                message=f"Command {status.value}",
                payload={"status": status.value},
            )
        return self._run_from_row(updated)

    def events_after(
        self,
        *,
        cursor: int,
        allowed_visibilities: set[EventVisibility] | None = None,
    ) -> list[EventEnvelope]:
        if cursor < 0:
            raise ValueError("cursor cannot be negative")
        statement = select(domain_events).where(
            domain_events.c.tenant_id == self._tenant_id,
            domain_events.c.cursor > cursor,
            domain_events.c.run_id.is_not(None),
        )
        if allowed_visibilities is not None:
            if not allowed_visibilities:
                return []
            statement = statement.where(
                domain_events.c.visibility.in_(
                    visibility.value for visibility in allowed_visibilities
                )
            )
        statement = statement.order_by(domain_events.c.cursor)
        with self._session.read() as connection:
            rows = connection.execute(statement).mappings().all()
        return [self._event_from_row(row) for row in rows]

    def claim(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        lease_until: datetime,
    ) -> CommandRun:
        normalized_worker = worker_id.strip()
        if not normalized_worker:
            raise ValueError("worker_id must not be empty")
        now = _now()
        normalized_lease_until = _datetime(lease_until)
        assert normalized_lease_until is not None
        if normalized_lease_until <= now:
            raise ValueError("lease_until must be in the future")
        with self._session.write() as connection:
            row = self._run_by_id(connection, run_id, for_update=True)
            if row is None:
                raise KeyError(f"command run not found: {run_id}")
            current = CommandStatus(row["status"])
            if current is not CommandStatus.QUEUED:
                raise InvalidTransitionError(f"cannot claim CommandRun from {current}")
            previous_fence = int(row["lease_fence"])
            result = connection.execute(
                update(command_runs)
                .where(
                    command_runs.c.tenant_id == self._tenant_id,
                    command_runs.c.id == str(run_id),
                    command_runs.c.status == CommandStatus.QUEUED.value,
                    command_runs.c.lease_fence == previous_fence,
                )
                .values(
                    status=CommandStatus.RUNNING.value,
                    lease_owner=normalized_worker,
                    lease_until=normalized_lease_until,
                    lease_fence=previous_fence + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise InvalidTransitionError("command claim changed concurrently")
            updated = self._run_by_id(connection, run_id)
            assert updated is not None
            self._append_event(
                connection,
                run=updated,
                event_type="command.running",
                visibility=EventVisibility.USER,
                message="Command running",
                payload={
                    "status": CommandStatus.RUNNING.value,
                    "lease_fence": previous_fence + 1,
                },
            )
        return self._run_from_row(updated)

    def claim_next(self, *, worker_id: str, lease_until: datetime) -> CommandRun | None:
        now = _now()
        if lease_until <= now:
            raise ValueError("lease_until must be in the future")
        with self._session.write() as connection:
            statement = (
                select(command_runs)
                .where(
                    command_runs.c.tenant_id == self._tenant_id,
                    or_(
                        command_runs.c.status == CommandStatus.QUEUED.value,
                        and_(
                            command_runs.c.status == CommandStatus.RUNNING.value,
                            command_runs.c.lease_until.is_not(None),
                            command_runs.c.lease_until <= now,
                        ),
                    ),
                )
                .order_by(command_runs.c.created_at, command_runs.c.id)
                .limit(1)
            )
            if self._session.dialect_name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            row = connection.execute(statement).mappings().first()
            if row is None:
                return None
            previous_status = CommandStatus(row["status"])
            previous_fence = int(row["lease_fence"])
            predicates = [
                command_runs.c.tenant_id == self._tenant_id,
                command_runs.c.id == row["id"],
                command_runs.c.status == previous_status.value,
                command_runs.c.lease_fence == previous_fence,
            ]
            if previous_status is CommandStatus.RUNNING:
                predicates.extend(
                    [
                        command_runs.c.lease_until.is_not(None),
                        command_runs.c.lease_until <= now,
                    ]
                )
            result = connection.execute(
                update(command_runs)
                .where(*predicates)
                .values(
                    status=CommandStatus.RUNNING.value,
                    lease_owner=worker_id,
                    lease_until=lease_until,
                    lease_fence=previous_fence + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                return None
            updated = self._run_by_id(connection, UUID(row["id"]))
            assert updated is not None
            self._append_event(
                connection,
                run=updated,
                event_type=(
                    "command.reclaimed"
                    if previous_status is CommandStatus.RUNNING
                    else "command.running"
                ),
                visibility=EventVisibility.USER,
                message=(
                    "Command reclaimed after worker interruption"
                    if previous_status is CommandStatus.RUNNING
                    else "Command running"
                ),
                payload={
                    "status": CommandStatus.RUNNING.value,
                    "lease_fence": previous_fence + 1,
                },
            )
        return self._run_from_row(updated)

    def _append_event(
        self,
        connection: Connection,
        *,
        run: Mapping[str, Any],
        event_type: str,
        visibility: EventVisibility,
        message: str,
        payload: dict[str, Any],
    ) -> EventEnvelope:
        sequence_statement = self._insert(task_event_sequences).values(
            tenant_id=self._tenant_id,
            task_id=run["task_id"],
            last_sequence=1,
        )
        sequence_statement = sequence_statement.on_conflict_do_update(
            index_elements=[
                task_event_sequences.c.tenant_id,
                task_event_sequences.c.task_id,
            ],
            set_={"last_sequence": task_event_sequences.c.last_sequence + 1},
        )
        sequence = int(
            connection.execute(
                sequence_statement.returning(task_event_sequences.c.last_sequence)
            ).scalar_one()
        )
        event_id = new_id()
        created_at = _now()
        scope = dict(run["scope"])
        cursor = connection.execute(
            self._insert(domain_events)
            .values(
                tenant_id=self._tenant_id,
                event_id=str(event_id),
                run_id=run["id"],
                user_id=run["actor"],
                device_id="core",
                project_id=run["project_id"],
                conversation_id=run["conversation_id"],
                task_id=run["task_id"],
                version_id=scope.get("target_version_id"),
                task_sequence=sequence,
                schema_version=1,
                event_type=event_type,
                visibility=visibility.value,
                message=message,
                payload=dict(payload),
                created_at=created_at,
            )
            .returning(domain_events.c.cursor)
        ).scalar_one()
        return EventEnvelope(
            id=event_id,
            cursor=int(cursor),
            run_id=UUID(run["id"]),
            project_id=UUID(run["project_id"]) if run["project_id"] else None,
            conversation_id=UUID(run["conversation_id"]),
            task_id=UUID(run["task_id"]),
            version_id=(
                UUID(scope["target_version_id"]) if scope.get("target_version_id") else None
            ),
            task_sequence=sequence,
            event_type=event_type,
            visibility=visibility,
            message=message,
            payload=dict(payload),
            schema_version=1,
            created_at=created_at,
        )

    def _insert(self, table: Any):
        return (
            postgresql_insert(table)
            if self._session.dialect_name == "postgresql"
            else sqlite_insert(table)
        )

    @staticmethod
    def _lease_predicates(
        row: Mapping[str, Any],
        *,
        lease_owner: str | None,
        lease_fence: int | None,
        now: datetime,
    ) -> list[Any]:
        current_owner = row["lease_owner"]
        if current_owner is None:
            if lease_owner is not None or lease_fence is not None:
                raise WorkerFenceError("command run has no active worker lease")
            return []
        if lease_owner != current_owner or lease_fence != int(row["lease_fence"]):
            raise WorkerFenceError("worker lease owner or fence does not match")
        lease_until = _datetime(row["lease_until"])
        if lease_until is None or lease_until <= now:
            raise WorkerFenceError("worker lease expired")
        return [
            command_runs.c.lease_owner == lease_owner,
            command_runs.c.lease_fence == lease_fence,
            command_runs.c.lease_until.is_not(None),
            command_runs.c.lease_until > now,
        ]

    def _run_by_id(
        self,
        connection: Connection,
        run_id: UUID,
        *,
        for_update: bool = False,
    ) -> RowMapping | None:
        statement = select(command_runs).where(
            command_runs.c.tenant_id == self._tenant_id,
            command_runs.c.id == str(run_id),
        )
        if for_update and self._session.dialect_name == "postgresql":
            statement = statement.with_for_update()
        return connection.execute(statement).mappings().first()

    def _run_by_idempotency_key(
        self,
        connection: Connection,
        idempotency_key: str,
    ) -> RowMapping | None:
        return (
            connection.execute(
                select(command_runs).where(
                    command_runs.c.tenant_id == self._tenant_id,
                    command_runs.c.idempotency_key == idempotency_key,
                )
            )
            .mappings()
            .first()
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
            "scope_digest": scope.scope_digest,
        }

    @staticmethod
    def _run_from_row(row: Mapping[str, Any]) -> CommandRun:
        return CommandRun(
            id=UUID(row["id"]),
            command_name=row["command_name"],
            actor=row["actor"],
            scope_digest=row["scope_digest"],
            project_id=UUID(row["project_id"]) if row["project_id"] else None,
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            input_payload=dict(row["input"]),
            risk_level=RiskLevel(row["risk_level"]),
            status=CommandStatus(row["status"]),
            idempotency_key=row["idempotency_key"],
            lease_owner=row["lease_owner"],
            lease_until=_datetime(row["lease_until"]),
            lease_fence=int(row["lease_fence"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _event_from_row(row: Mapping[str, Any]) -> EventEnvelope:
        return EventEnvelope(
            id=UUID(row["event_id"]),
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
            payload=dict(row["payload"]),
            schema_version=int(row["schema_version"]),
            created_at=_datetime(row["created_at"]),
        )
