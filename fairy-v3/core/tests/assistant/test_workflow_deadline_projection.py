from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import update

from fairy_core.providers import CancellationToken, ProviderRegistry
from fairy_core.storage.schema import workflow_runs
from fairy_core.transports.stdio import build_local_service
from fairy_core.workflow.errors import WorkflowFenceError
from fairy_core.workflow.scheduler import _ActiveNode
from tests.assistant.test_application import _scratch_task, _turn
from tests.assistant.test_step_workflow import _provider


@pytest.mark.parametrize("engine_version", [3, 4])
@pytest.mark.parametrize("maintenance", ["claim", "renew"])
def test_deadline_settles_only_its_own_turn_and_task(tmp_path, engine_version, maintenance):
    provider = _provider()
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((provider,)),
        assistant_workflow_engine_version=engine_version,
    )
    kernel = service._workflow_scheduler
    kernel.close()
    try:
        task = _scratch_task(service, "This task expires")
        turn = _turn(service, task, "expired")
        other_task = _scratch_task(service, "This task stays available")
        other = _turn(service, other_task, "not-expired")
        run_id = UUID(turn["workflow_run_id"])
        if maintenance != "claim":
            with service._unit_of_work_factory() as unit:
                unit.workflows.resume(run_id)
                (claim,) = unit.workflows.claim_ready(
                    worker_id="held", lease_until=datetime.now(UTC) + timedelta(minutes=1),
                    limit=1,
                )
                node = unit.workflows.get_node(run_id, claim.node_id)
                unit.commit()
            kernel._active[node.id] = _ActiveNode(
                claim, CancellationToken(), node.kind, None, node,
            )
            kernel._last_heartbeat = 0
        with service._unit_of_work_factory() as unit:
            unit._connection.execute(update(workflow_runs).where(
                workflow_runs.c.id == str(run_id), workflow_runs.c.tenant_id == "local",
            ).values(created_at=datetime.now(UTC) - timedelta(hours=3)))
            unit.commit()
        if maintenance == "claim":
            kernel._claim_available()
        elif maintenance == "renew":
            kernel._heartbeat_if_due()
        else:
            adapter = kernel._adapters.require("assistant.step.model")
            with pytest.raises(WorkflowFenceError):
                adapter.execute_claimed(node, claim, CancellationToken())
        with service._unit_of_work_factory() as unit:
            expired = unit.workflows.get_run(run_id)
            projected = unit.assistant.get_turn(UUID(turn["id"]))
            assert unit.assistant.get_turn(UUID(other["id"])).status == other["status"]
            assert unit.state.get_task(UUID(other_task["id"])).status == "planning"
            assert expired.status == projected.status == "failed"
            assert projected.error_code == "WORKFLOW_DEADLINE_EXCEEDED"
            assert unit.state.get_task(UUID(task["id"])).status == "failed"
        assert not provider.requests
    finally:
        kernel._active.clear()
        service.close()


def test_deadline_at_adapter_entry_settles_without_dispatch(tmp_path):
    test_deadline_settles_only_its_own_turn_and_task(tmp_path, 4, "entry")


def test_failed_projection_rolls_back_kernel_deadline(tmp_path, monkeypatch):
    service = build_local_service(tmp_path, assistant_workflow_engine_version=4)
    kernel = service._workflow_scheduler
    kernel.close()
    try:
        task = _scratch_task(service, "Atomic timeout")
        turn = _turn(service, task, "atomic-timeout")
        run_id = UUID(turn["workflow_run_id"])
        with service._unit_of_work_factory() as unit:
            unit._connection.execute(update(workflow_runs).where(
                workflow_runs.c.id == str(run_id), workflow_runs.c.tenant_id == "local",
            ).values(created_at=datetime.now(UTC) - timedelta(hours=3)))
            unit.commit()

        def fail_projection(unit, run):
            raise RuntimeError("Injected projection failure")

        with monkeypatch.context() as patch:
            patch.setattr(kernel._adapters, "settle_failed_run", fail_projection)
            with pytest.raises(RuntimeError, match="Injected projection"):
                kernel._claim_available()
        with service._unit_of_work_factory() as unit:
            assert unit.workflows.get_run(run_id).status == "paused"
            assert unit.assistant.get_turn(UUID(turn["id"])).status == "created"
        kernel._claim_available()
        with service._unit_of_work_factory() as unit:
            assert unit.workflows.get_run(run_id).status == "failed"
            assert unit.assistant.get_turn(UUID(turn["id"])).status == "failed"
    finally:
        service.close()


@pytest.mark.parametrize("engine_version", [3, 4])
def test_startup_repairs_old_failed_run_projection_once(tmp_path, engine_version):
    service = build_local_service(tmp_path, assistant_workflow_engine_version=engine_version)
    service._workflow_scheduler.close()
    try:
        task = _scratch_task(service, "Recover this failed task")
        turn = _turn(service, task, "old-failure")
        other_task = _scratch_task(service, "Do not change this other task")
        other = _turn(service, other_task, "other")
        with service._unit_of_work_factory() as unit:
            unit.workflows.resume(UUID(turn["workflow_run_id"]))
            (claim,) = unit.workflows.claim_ready(
                worker_id="old-core", lease_until=datetime.now(UTC) + timedelta(minutes=1),
                limit=1,
            )
            unit.workflows.fail(claim, error_code="WORKFLOW_DEADLINE_EXCEEDED")
            unit.commit()
    finally:
        service.close()
    for _ in range(2):
        provider = _provider()
        service = build_local_service(tmp_path, provider_registry=ProviderRegistry((provider,)))
        try:
            with service._unit_of_work_factory() as unit:
                assert unit.assistant.get_turn(UUID(turn["id"])).status == "failed"
                assert unit.assistant.get_turn(UUID(other["id"])).status == other["status"]
                events = [event for event in unit.commands.events_after(cursor=0) if (
                    event.event_type == "assistant.turn.failed"
                    and event.payload.get("turn_id") == turn["id"]
                )]
                assert len(events) == 1
            assert not provider.requests
        finally:
            service.close()
