from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from fairy_core.commanding import (
    CommandStatus,
    EventVisibility,
    SqlAlchemyCommandLedger,
    SqliteCommandLedger,
)
from fairy_core.commanding.registry import RiskLevel
from fairy_core.domain.errors import (
    IdempotencyConflictError,
    InvalidTransitionError,
    WorkerFenceError,
)
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import OperationMode, ScopeContract, WorkspaceType
from fairy_core.persistence.sqlite import create_sqlite_core_engine


def _scope(tmp_path: Path) -> ScopeContract:
    project_id, conversation_id, task_id, base_id, target_id = [new_id() for _ in range(5)]
    target_root = tmp_path / "target"
    return ScopeContract.create(
        workspace_type=WorkspaceType.PROJECT_CHAT,
        project_id=project_id,
        conversation_id=conversation_id,
        task_id=task_id,
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=base_id,
        target_version_id=target_id,
        project_root=target_root,
        allowed_write_paths=(target_root,),
        forbidden_write_paths=(tmp_path / "other",),
        execution_target="local",
        network_policy="project_safe",
        memory_read_scope=("project_canonical",),
        memory_write_scope=("current_conversation_draft",),
    )


def test_sqlite_ledger_migrates_pre_tenant_v3_runs_and_events(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    scope = _scope(tmp_path)
    run_id = new_id()
    event_id = new_id()
    now = datetime.now(UTC).isoformat()
    scope_payload = {
        "workspace_type": scope.workspace_type.value,
        "project_id": str(scope.project_id),
        "conversation_id": str(scope.conversation_id),
        "task_id": str(scope.task_id),
        "operation_mode": scope.operation_mode.value,
        "base_version_id": str(scope.base_version_id),
        "target_version_id": str(scope.target_version_id),
        "project_root": str(scope.project_root),
        "allowed_write_paths": [str(path) for path in scope.allowed_write_paths],
        "forbidden_write_paths": [str(path) for path in scope.forbidden_write_paths],
        "execution_target": scope.execution_target,
        "network_policy": scope.network_policy,
        "memory_read_scope": list(scope.memory_read_scope),
        "memory_write_scope": list(scope.memory_write_scope),
    }
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE command_runs (
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
            CREATE TABLE command_events (
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
                UNIQUE(task_id, task_sequence)
            );
            """
        )
        connection.execute(
            """
            INSERT INTO command_runs (
                id, command_name, actor, scope_json, scope_digest, project_id,
                conversation_id, task_id, input_json, risk_level, status,
                idempotency_key, lease_owner, lease_until, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(run_id),
                "project.read",
                "agent",
                json.dumps(scope_payload, sort_keys=True),
                scope.scope_digest,
                str(scope.project_id),
                str(scope.conversation_id),
                str(scope.task_id),
                json.dumps({"query": "entrypoints"}, sort_keys=True),
                RiskLevel.LOW.value,
                CommandStatus.RUNNING.value,
                "legacy-key",
                None,
                None,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO command_events (
                cursor, id, run_id, project_id, conversation_id, task_id,
                version_id, task_sequence, event_type, visibility, message,
                payload_json, schema_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                7,
                str(event_id),
                str(run_id),
                str(scope.project_id),
                str(scope.conversation_id),
                str(scope.task_id),
                str(scope.target_version_id),
                3,
                "command.created",
                EventVisibility.USER.value,
                "Command created: project.read",
                json.dumps({"status": CommandStatus.CREATED.value}),
                1,
                now,
            ),
        )

    ledger = SqliteCommandLedger(path)
    migrated = ledger.get_run(run_id)
    migrated_events = ledger.events_after(cursor=0)
    with sqlite3.connect(path) as connection:
        applied_at = connection.execute("SELECT applied_at FROM core_local_migrations").fetchone()[
            0
        ]
    appended = ledger.append_event(
        run_id=run_id,
        event_type="command.queued",
        visibility=EventVisibility.USER,
        message="Command queued",
        payload={"status": CommandStatus.QUEUED.value},
    )

    assert migrated is not None
    assert migrated.input_payload == {"query": "entrypoints"}
    assert migrated.status is CommandStatus.INTERRUPTED
    assert migrated.lease_owner is None
    assert migrated.lease_until is None
    assert [event.cursor for event in migrated_events] == [7, 8]
    assert [event.task_sequence for event in migrated_events] == [3, 4]
    assert migrated_events[-1].event_type == "command.interrupted"
    assert migrated_events[-1].payload == {
        "reason": "missing_worker_lease",
        "status": CommandStatus.INTERRUPTED.value,
    }
    assert appended.cursor == 9
    assert appended.task_sequence == 5
    assert datetime.fromisoformat(applied_at).utcoffset() == timedelta(0)


def test_ledger_recovers_run_and_events_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    scope = _scope(tmp_path)
    first = SqliteCommandLedger(path)
    run = first.create_run(
        command_name="project.read",
        actor="agent",
        scope=scope,
        input_payload={"query": "entrypoints"},
        risk_level=RiskLevel.LOW,
        idempotency_key="request-1:project.read",
    )
    first.append_event(
        run_id=run.id,
        event_type="command.queued",
        visibility=EventVisibility.USER,
        message="Searching project files",
        payload={"stage": "search"},
    )
    first.close()

    restarted = SqliteCommandLedger(path)
    recovered = restarted.get_run(run.id)
    events = restarted.events_after(cursor=0, allowed_visibilities={EventVisibility.USER})

    assert recovered is not None
    assert recovered.idempotency_key == "request-1:project.read"
    assert recovered.scope_digest == scope.scope_digest
    assert [event.event_type for event in events] == ["command.created", "command.queued"]
    assert [event.cursor for event in events] == [1, 2]
    assert [event.task_sequence for event in events] == [1, 2]


def test_ledger_persists_memory_snapshot_binding_in_scope(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    unbound = _scope(tmp_path)
    snapshot_id = new_id()
    snapshot_hash = "a" * 64
    scope = ScopeContract.create(
        workspace_type=unbound.workspace_type,
        project_id=unbound.project_id,
        conversation_id=unbound.conversation_id,
        task_id=unbound.task_id,
        operation_mode=unbound.operation_mode,
        base_version_id=unbound.base_version_id,
        target_version_id=unbound.target_version_id,
        project_root=unbound.project_root,
        allowed_write_paths=unbound.allowed_write_paths,
        forbidden_write_paths=unbound.forbidden_write_paths,
        execution_target=unbound.execution_target,
        network_policy=unbound.network_policy,
        memory_read_scope=unbound.memory_read_scope,
        memory_write_scope=unbound.memory_write_scope,
        memory_snapshot_id=snapshot_id,
        memory_snapshot_hash=snapshot_hash,
    )
    ledger = SqliteCommandLedger(path)

    ledger.create_run(
        command_name="project.read",
        actor="agent",
        scope=scope,
        input_payload={},
        risk_level=RiskLevel.LOW,
        idempotency_key="snapshot-scope",
    )
    ledger.close()

    with sqlite3.connect(path) as connection:
        stored_scope = json.loads(
            connection.execute("SELECT scope FROM command_runs").fetchone()[0]
        )
    assert stored_scope["memory_snapshot_id"] == str(snapshot_id)
    assert stored_scope["memory_snapshot_hash"] == snapshot_hash


def test_current_cursor_reads_zero_and_latest_tenant_event(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "cursor.db")
    ledger = SqlAlchemyCommandLedger(engine, tenant_id="local")

    assert ledger.current_cursor() == 0

    scope = _scope(tmp_path)
    run = ledger.create_run(
        command_name="workspace.diff",
        actor="core",
        scope=scope,
        input_payload={},
        risk_level=RiskLevel.LOW,
        idempotency_key="cursor:run",
    )
    latest = ledger.events_for_run(run.id)[-1].cursor

    assert ledger.current_cursor() == latest


def test_idempotency_key_returns_existing_run_without_duplicate_event(tmp_path: Path) -> None:
    ledger = SqliteCommandLedger(tmp_path / "ledger.db")
    scope = _scope(tmp_path)

    first = ledger.create_run(
        command_name="project.read",
        actor="agent",
        scope=scope,
        input_payload={},
        risk_level=RiskLevel.LOW,
        idempotency_key="same-key",
    )
    second = ledger.create_run(
        command_name="project.read",
        actor="agent",
        scope=scope,
        input_payload={},
        risk_level=RiskLevel.LOW,
        idempotency_key="same-key",
    )

    assert second.id == first.id
    assert len(ledger.events_after(cursor=0)) == 1


def test_idempotency_key_rejects_a_different_request(tmp_path: Path) -> None:
    ledger = SqliteCommandLedger(tmp_path / "ledger.db")
    scope = _scope(tmp_path)
    ledger.create_run(
        command_name="project.read",
        actor="agent",
        scope=scope,
        input_payload={"query": "first"},
        risk_level=RiskLevel.LOW,
        idempotency_key="same-key",
    )

    with pytest.raises(IdempotencyConflictError):
        ledger.create_run(
            command_name="project.read",
            actor="agent",
            scope=scope,
            input_payload={"query": "different"},
            risk_level=RiskLevel.LOW,
            idempotency_key="same-key",
        )


def test_sqlalchemy_ledger_isolates_tenant_keys_and_event_cursors(tmp_path: Path) -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tenant_a = SqlAlchemyCommandLedger(
        engine,
        tenant_id="tenant-a",
        initialize_schema=True,
    )
    tenant_b = SqlAlchemyCommandLedger(engine, tenant_id="tenant-b")
    scope = _scope(tmp_path)

    run_a = tenant_a.create_run(
        command_name="project.read",
        actor="agent",
        scope=scope,
        input_payload={},
        risk_level=RiskLevel.LOW,
        idempotency_key="same-key",
    )
    run_b = tenant_b.create_run(
        command_name="project.read",
        actor="agent",
        scope=scope,
        input_payload={},
        risk_level=RiskLevel.LOW,
        idempotency_key="same-key",
    )

    assert run_a.id != run_b.id
    assert [event.run_id for event in tenant_a.events_after(cursor=0)] == [run_a.id]
    assert [event.run_id for event in tenant_b.events_after(cursor=0)] == [run_b.id]


def test_command_run_rejects_invalid_transition(tmp_path: Path) -> None:
    ledger = SqliteCommandLedger(tmp_path / "ledger.db")
    run = ledger.create_run(
        command_name="edit.apply_changeset",
        actor="agent",
        scope=_scope(tmp_path),
        input_payload={},
        risk_level=RiskLevel.MEDIUM,
        idempotency_key="transition",
    )

    with pytest.raises(InvalidTransitionError):
        ledger.transition(run.id, CommandStatus.SUCCEEDED)


def test_running_transition_requires_a_worker_lease(tmp_path: Path) -> None:
    ledger = SqliteCommandLedger(tmp_path / "ledger.db")
    run = ledger.create_run(
        command_name="review.test",
        actor="core",
        scope=_scope(tmp_path),
        input_payload={},
        risk_level=RiskLevel.LOW,
        idempotency_key="running-requires-lease",
    )
    queued = ledger.transition(run.id, CommandStatus.QUEUED)

    with pytest.raises(InvalidTransitionError, match="claim"):
        ledger.transition(queued.id, CommandStatus.RUNNING)

    assert ledger.get_run(run.id).status is CommandStatus.QUEUED


def test_event_visibility_filters_internal_events(tmp_path: Path) -> None:
    ledger = SqliteCommandLedger(tmp_path / "ledger.db")
    run = ledger.create_run(
        command_name="project.read",
        actor="agent",
        scope=_scope(tmp_path),
        input_payload={},
        risk_level=RiskLevel.LOW,
        idempotency_key="visibility",
    )
    ledger.append_event(
        run_id=run.id,
        event_type="agent.internal",
        visibility=EventVisibility.INTERNAL,
        message="private planner state",
        payload={},
    )

    visible = ledger.events_after(cursor=0, allowed_visibilities={EventVisibility.USER})

    assert [event.event_type for event in visible] == ["command.created"]
    assert all(event.visibility is EventVisibility.USER for event in visible)


def test_only_one_worker_can_claim_a_queued_run(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    first = SqliteCommandLedger(path)
    second = SqliteCommandLedger(path)
    run = first.create_run(
        command_name="review.test",
        actor="core",
        scope=_scope(tmp_path),
        input_payload={},
        risk_level=RiskLevel.LOW,
        idempotency_key="claim",
    )
    first.transition(run.id, CommandStatus.QUEUED)
    now = datetime.now(UTC)

    claimed = first.claim_next(worker_id="worker-a", lease_until=now + timedelta(seconds=30))
    unavailable = second.claim_next(worker_id="worker-b", lease_until=now + timedelta(seconds=30))

    assert claimed is not None
    assert claimed.id == run.id
    assert claimed.status is CommandStatus.RUNNING
    assert claimed.lease_owner == "worker-a"
    assert unavailable is None


def test_expired_worker_lease_is_reclaimed_with_a_higher_fence(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    first = SqliteCommandLedger(path)
    second = SqliteCommandLedger(path)
    run = first.create_run(
        command_name="review.test",
        actor="core",
        scope=_scope(tmp_path),
        input_payload={},
        risk_level=RiskLevel.LOW,
        idempotency_key="reclaim",
    )
    first.transition(run.id, CommandStatus.QUEUED)
    first_claim = first.claim_next(
        worker_id="worker-a",
        lease_until=datetime.now(UTC) + timedelta(milliseconds=5),
    )
    time.sleep(0.02)

    second_claim = second.claim_next(
        worker_id="worker-b",
        lease_until=datetime.now(UTC) + timedelta(seconds=30),
    )

    assert first_claim is not None
    assert second_claim is not None
    assert second_claim.id == first_claim.id
    assert second_claim.lease_owner == "worker-b"
    assert second_claim.lease_fence == first_claim.lease_fence + 1


def test_stale_worker_cannot_append_an_event_after_reclaim(tmp_path: Path) -> None:
    ledger = SqliteCommandLedger(tmp_path / "ledger.db")
    run = ledger.create_run(
        command_name="review.test",
        actor="core",
        scope=_scope(tmp_path),
        input_payload={},
        risk_level=RiskLevel.LOW,
        idempotency_key="stale-event",
    )
    ledger.transition(run.id, CommandStatus.QUEUED)
    first_claim = ledger.claim_next(
        worker_id="worker-a",
        lease_until=datetime.now(UTC) + timedelta(milliseconds=5),
    )
    time.sleep(0.02)
    second_claim = ledger.claim_next(
        worker_id="worker-b",
        lease_until=datetime.now(UTC) + timedelta(seconds=30),
    )
    assert first_claim is not None
    assert second_claim is not None
    events_before_stale_append = ledger.events_after(cursor=0)

    with pytest.raises(WorkerFenceError):
        ledger.append_event(
            run_id=run.id,
            event_type="command.output",
            visibility=EventVisibility.DEVELOPER,
            message="stale output",
            payload={},
            lease_owner="worker-a",
            lease_fence=first_claim.lease_fence,
        )

    assert ledger.events_after(cursor=0) == events_before_stale_append
    current_event = ledger.append_event(
        run_id=run.id,
        event_type="command.progress",
        visibility=EventVisibility.USER,
        message="current progress",
        payload={"percent": 50},
        lease_owner="worker-b",
        lease_fence=second_claim.lease_fence,
    )
    assert current_event.payload == {"percent": 50}


def test_stale_worker_cannot_transition_after_reclaim(tmp_path: Path) -> None:
    ledger = SqliteCommandLedger(tmp_path / "ledger.db")
    run = ledger.create_run(
        command_name="review.test",
        actor="core",
        scope=_scope(tmp_path),
        input_payload={},
        risk_level=RiskLevel.LOW,
        idempotency_key="stale-transition",
    )
    ledger.transition(run.id, CommandStatus.QUEUED)
    first_claim = ledger.claim_next(
        worker_id="worker-a",
        lease_until=datetime.now(UTC) + timedelta(milliseconds=5),
    )
    time.sleep(0.02)
    second_claim = ledger.claim_next(
        worker_id="worker-b",
        lease_until=datetime.now(UTC) + timedelta(seconds=30),
    )
    assert first_claim is not None
    assert second_claim is not None
    events_before_stale_transition = ledger.events_after(cursor=0)

    with pytest.raises(WorkerFenceError):
        ledger.transition(
            run.id,
            CommandStatus.SUCCEEDED,
            lease_owner="worker-a",
            lease_fence=first_claim.lease_fence,
        )

    assert ledger.get_run(run.id).status is CommandStatus.RUNNING
    assert ledger.events_after(cursor=0) == events_before_stale_transition
    succeeded = ledger.transition(
        run.id,
        CommandStatus.SUCCEEDED,
        lease_owner="worker-b",
        lease_fence=second_claim.lease_fence,
    )
    assert succeeded.status is CommandStatus.SUCCEEDED
