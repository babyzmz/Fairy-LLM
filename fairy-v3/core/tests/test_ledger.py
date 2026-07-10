from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fairy_core.commanding.ledger import (
    CommandStatus,
    EventVisibility,
    SqliteCommandLedger,
)
from fairy_core.commanding.registry import RiskLevel
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import OperationMode, ScopeContract, WorkspaceType


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
