from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from fairy_core.domain.models import Project, ProjectResidency
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.workflow.errors import WorkflowFenceError, WorkflowRevisionError
from fairy_core.workflow.models import WorkflowNodeStatus, WorkflowRunStatus
from fairy_core.workflow.scheduler import (
    WorkflowAdapterRegistry,
    WorkflowNodeResult,
    WorkflowScheduler,
)
from tests.workflow.test_pause_settlement import _claimed


def test_fenced_checkpoint_survives_abandon_reopen_and_claim_without_becoming_complete(tmp_path):
    path = tmp_path / "checkpoint.db"
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="left")
    run, claim = _claimed(factory)
    result = {"schema_version": 1, "text": "confirmed result", "calls": []}
    try:
        with factory() as unit:
            assert unit.workflows.record_checkpoint(claim, result=result)
            assert not unit.workflows.record_checkpoint(claim, result=result)
            snapshot = unit.workflows.get(run.id)
            assert snapshot.run.status is WorkflowRunStatus.RUNNING
            assert snapshot.nodes[0].status is WorkflowNodeStatus.RUNNING
            unit.commit()
        with pytest.raises(WorkflowRevisionError), factory() as unit:
            unit.workflows.record_checkpoint(claim, result={"text": "replacement"})
        with factory() as unit:
            assert unit.workflows.abandon(claim)
            unit.commit()
    finally:
        engine.dispose()
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="left")
    try:
        with factory() as unit:
            claims = unit.workflows.claim_ready(
                worker_id="restarted",
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
                limit=1,
            )
            snapshot = unit.workflows.get(run.id)
            unit.commit()
        assert len(claims) == 1 and claims[0].lease_fence > claim.lease_fence
        assert dict(snapshot.nodes[0].result) == result
        with pytest.raises(WorkflowFenceError), factory() as unit:
            unit.workflows.record_checkpoint(claim, result=result)
        with (
            pytest.raises((WorkflowFenceError, KeyError)),
            SqlAlchemyUnitOfWorkFactory(engine, tenant_id="right")() as unit,
        ):
            unit.workflows.record_checkpoint(claims[0], result=result)
    finally:
        engine.dispose()


def test_invalid_checkpoint_fence_rolls_back_domain_changes(tmp_path):
    engine = create_sqlite_core_engine(tmp_path / "atomic.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    run, claim = _claimed(factory)
    project = Project.create(name="Must roll back", residency=ProjectResidency.LOCAL_ONLY)
    try:
        with pytest.raises(WorkflowFenceError), factory() as unit:
            unit.state.save_project(project)
            unit.workflows.record_checkpoint(
                replace(claim, lease_fence=999), result={"text": "bad"}
            )
            unit.commit()
        with factory() as unit:
            assert unit.state.get_project(project.id) is None
            assert unit.workflows.get(run.id).nodes[0].result is None
    finally:
        engine.dispose()


def test_scheduler_delivers_actual_claim_to_checkpoint_adapter(tmp_path):
    engine = create_sqlite_core_engine(tmp_path / "adapter.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    run, initial = _claimed(factory)
    with factory() as unit:
        unit.workflows.abandon(initial)
        unit.commit()

    class Adapter:
        def execute(self, node, cancellation):
            raise AssertionError("This adapter requires the actual claimed fence")

        def execute_claimed(self, node, claim, cancellation):
            cancellation.raise_if_cancelled()
            assert claim.node_id == node.id and claim.run_id == node.run_id
            with factory() as unit:
                unit.workflows.record_checkpoint(claim, result={"fence": claim.lease_fence})
                unit.commit()
            return WorkflowNodeResult(output={"fence": claim.lease_fence})

    scheduler = WorkflowScheduler(
        unit_of_work_factory=factory,
        adapters=WorkflowAdapterRegistry({"test.echo": Adapter()}),
        autostart=False,
    )
    try:
        scheduler.start()
        snapshot = scheduler.wait(run.id, timeout=3)
        assert snapshot.run.status is WorkflowRunStatus.COMPLETED
        assert snapshot.nodes[0].result == {"fence": 2}
    finally:
        scheduler.close()
        engine.dispose()


@pytest.mark.parametrize("payload", [{"number": float("nan")}, {"text": "界" * 700_000}])
def test_checkpoint_rejects_non_json_or_oversized_payload_without_partial_state(tmp_path, payload):
    engine = create_sqlite_core_engine(tmp_path / "bounded.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    run, claim = _claimed(factory)
    try:
        with factory() as unit:
            with pytest.raises(ValueError):
                unit.workflows.record_checkpoint(claim, result=payload)
            unit.commit()
        with factory() as unit:
            snapshot = unit.workflows.get(run.id)
        assert snapshot.nodes[0].result is None
        assert snapshot.nodes[0].status is WorkflowNodeStatus.RUNNING
    finally:
        engine.dispose()


@pytest.mark.parametrize("max_bytes", [True, 0, -1, 1.5, 8 * 1024 * 1024 + 1])
def test_checkpoint_adapter_cannot_remove_the_global_byte_bound(tmp_path, max_bytes):
    engine = create_sqlite_core_engine(tmp_path / "adapter-budget.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    run, claim = _claimed(factory)
    try:
        with factory() as unit:
            with pytest.raises(ValueError):
                unit.workflows.record_checkpoint(claim, result={"ok": True}, max_bytes=max_bytes)
            unit.commit()
        with factory() as unit:
            assert unit.workflows.get_node(run.id, claim.node_id).result is None
    finally:
        engine.dispose()
