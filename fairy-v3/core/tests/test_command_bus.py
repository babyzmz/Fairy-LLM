from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fairy_core.commanding import CommandStatus, SqliteCommandLedger
from fairy_core.commanding.bus import CommandBus, CommandRequest
from fairy_core.commanding.policy import PermissionProfile, PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.domain.errors import DomainError
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import OperationMode, ScopeContract, WorkspaceType


def _scope(tmp_path: Path) -> ScopeContract:
    root = tmp_path / "version"
    return ScopeContract.create(
        workspace_type=WorkspaceType.PROJECT_CHAT,
        project_id=new_id(),
        conversation_id=new_id(),
        task_id=new_id(),
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=new_id(),
        target_version_id=new_id(),
        project_root=root,
        allowed_write_paths=(root,),
        forbidden_write_paths=(),
        execution_target="local",
        network_policy="project_safe",
        memory_read_scope=("project_canonical",),
        memory_write_scope=("current_conversation_draft",),
    )


def _bus(tmp_path: Path) -> tuple[CommandBus, SqliteCommandLedger]:
    registry = build_default_registry()
    ledger = SqliteCommandLedger(tmp_path / "ledger.db")
    return CommandBus(registry=registry, policy=PolicyEngine(registry), ledger=ledger), ledger


def test_standard_write_command_is_persisted_waiting_for_approval(tmp_path: Path) -> None:
    bus, ledger = _bus(tmp_path)

    result = bus.submit(
        CommandRequest(
            tool_name="edit.apply_changeset",
            actor="agent",
            scope=_scope(tmp_path),
            payload={"changeset_id": str(new_id())},
            idempotency_key="request:apply",
        ),
        profile=PermissionProfile.STANDARD,
        capability_overrides={},
        sandbox_healthy=False,
    )

    assert result.accepted is True
    assert result.requires_approval is True
    assert result.run is not None
    assert ledger.get_run(result.run.id).status is CommandStatus.WAITING_APPROVAL


def test_approval_decision_queues_existing_run(tmp_path: Path) -> None:
    bus, ledger = _bus(tmp_path)
    submitted = bus.submit(
        CommandRequest(
            tool_name="edit.apply_changeset",
            actor="agent",
            scope=_scope(tmp_path),
            payload={},
            idempotency_key="request:approve",
        ),
        profile=PermissionProfile.STANDARD,
        capability_overrides={},
        sandbox_healthy=False,
    )

    queued = bus.decide_approval(submitted.run.id, approved=True)

    assert queued.status is CommandStatus.QUEUED
    assert ledger.get_run(queued.id).status is CommandStatus.QUEUED


def test_autonomous_sandbox_command_is_queued_without_approval(tmp_path: Path) -> None:
    bus, _ledger = _bus(tmp_path)

    result = bus.submit(
        CommandRequest(
            tool_name="run.sandboxed",
            actor="agent",
            scope=_scope(tmp_path),
            payload={"script": "npm test"},
            idempotency_key="request:sandbox",
        ),
        profile=PermissionProfile.AUTONOMOUS,
        capability_overrides={"run.sandboxed": True},
        sandbox_healthy=True,
    )

    assert result.accepted is True
    assert result.requires_approval is False
    assert result.run.status is CommandStatus.QUEUED


def test_unavailable_capability_is_rejected_without_creating_run(tmp_path: Path) -> None:
    bus, ledger = _bus(tmp_path)

    result = bus.submit(
        CommandRequest(
            tool_name="run.sandboxed",
            actor="agent",
            scope=_scope(tmp_path),
            payload={},
            idempotency_key="request:blocked",
        ),
        profile=PermissionProfile.OBSERVE,
        capability_overrides={},
        sandbox_healthy=True,
    )

    assert result.accepted is False
    assert result.error_code == "CAPABILITY_NOT_AVAILABLE"
    assert result.run is None
    assert ledger.events_after(cursor=0) == []


def test_bus_records_synchronous_executor_lifecycle(tmp_path: Path) -> None:
    bus, ledger = _bus(tmp_path)
    submitted = bus.submit(
        CommandRequest(
            tool_name="review.test",
            actor="core",
            scope=_scope(tmp_path),
            payload={"suite": "focused"},
            idempotency_key="request:review",
        ),
        profile=PermissionProfile.STANDARD,
        capability_overrides={},
        sandbox_healthy=True,
    )

    running = bus.start(submitted.run.id)
    succeeded = bus.complete(
        running.id,
        output={"passed": 12},
        lease_owner=running.lease_owner,
        lease_fence=running.lease_fence,
    )

    assert running.status is CommandStatus.RUNNING
    assert succeeded.status is CommandStatus.SUCCEEDED
    events = ledger.events_after(cursor=0)
    assert [event.event_type for event in events][-2:] == [
        "command.output",
        "command.succeeded",
    ]
    assert events[-2].payload == {"passed": 12}


def test_bus_records_executor_failure_without_exposing_exception_details(tmp_path: Path) -> None:
    bus, ledger = _bus(tmp_path)
    submitted = bus.submit(
        CommandRequest(
            tool_name="review.test",
            actor="core",
            scope=_scope(tmp_path),
            payload={},
            idempotency_key="request:review-failed",
        ),
        profile=PermissionProfile.STANDARD,
        capability_overrides={},
        sandbox_healthy=True,
    )

    running = bus.start(submitted.run.id)
    failed = bus.fail(
        submitted.run.id,
        error_code="WORKER_INTERRUPTED",
        lease_owner=running.lease_owner,
        lease_fence=running.lease_fence,
    )

    assert failed.status is CommandStatus.FAILED
    failure_event = ledger.events_after(cursor=0)[-2]
    assert failure_event.event_type == "command.failure"
    assert failure_event.payload == {"error_code": "WORKER_INTERRUPTED"}


def test_stale_worker_cannot_complete_a_reclaimed_run(tmp_path: Path) -> None:
    bus, ledger = _bus(tmp_path)
    submitted = bus.submit(
        CommandRequest(
            tool_name="review.test",
            actor="core",
            scope=_scope(tmp_path),
            payload={},
            idempotency_key="request:stale-worker",
        ),
        profile=PermissionProfile.STANDARD,
        capability_overrides={},
        sandbox_healthy=True,
    )
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
    events_before_stale_completion = ledger.events_after(cursor=0)

    with pytest.raises(DomainError) as error:
        bus.complete(
            submitted.run.id,
            output={"passed": 12},
            lease_owner="worker-a",
            lease_fence=first_claim.lease_fence,
        )

    assert error.value.code == "WORKER_INTERRUPTED"
    assert ledger.get_run(submitted.run.id).status is CommandStatus.RUNNING
    assert ledger.events_after(cursor=0) == events_before_stale_completion

    succeeded = bus.complete(
        submitted.run.id,
        output={"passed": 12},
        lease_owner="worker-b",
        lease_fence=second_claim.lease_fence,
    )
    assert succeeded.status is CommandStatus.SUCCEEDED


def test_bus_start_claims_a_recoverable_worker_lease(tmp_path: Path) -> None:
    bus, ledger = _bus(tmp_path)
    submitted = bus.submit(
        CommandRequest(
            tool_name="review.test",
            actor="core",
            scope=_scope(tmp_path),
            payload={},
            idempotency_key="request:recoverable-start",
        ),
        profile=PermissionProfile.STANDARD,
        capability_overrides={},
        sandbox_healthy=True,
    )

    running = bus.start(
        submitted.run.id,
        worker_id="core-a",
        lease_until=datetime.now(UTC) + timedelta(milliseconds=5),
    )
    time.sleep(0.02)
    reclaimed = ledger.claim_next(
        worker_id="core-b",
        lease_until=datetime.now(UTC) + timedelta(seconds=30),
    )

    assert running.status is CommandStatus.RUNNING
    assert running.lease_owner == "core-a"
    assert running.lease_fence == 1
    assert reclaimed is not None
    assert reclaimed.id == running.id
    assert reclaimed.lease_owner == "core-b"
    assert reclaimed.lease_fence == running.lease_fence + 1
