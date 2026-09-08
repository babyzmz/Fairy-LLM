from datetime import UTC, datetime, timedelta

import pytest

from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.workflow import repository
from fairy_core.workflow.errors import WorkflowFenceError
from fairy_core.workflow.models import WorkflowEdge
from fairy_core.workflow.scheduler import WorkflowAdapterRegistry
from tests.workflow.test_repository import _node, _run


@pytest.mark.parametrize("cancelled", [False, True])
def test_only_declared_durable_reconciliation_can_claim_while_paused(
    tmp_path, monkeypatch, cancelled,
):
    path = tmp_path / "reconciliation.db"
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="left")
    run, other = _run("existing-operation"), _run("unstarted-operation")
    node, next_node = _node(run, "receipt"), _node(run, "next-operation")
    unrelated = _node(other, "unstarted")
    phases = {node.kind: "existing-outcome-v1"}
    checkpoint = {"reconciliation_phase": phases[node.kind], "receipt_id": "existing"}
    now = datetime.now(UTC)
    due = now + timedelta(seconds=2)
    try:
        with factory() as unit:
            unit.workflows.create(run, nodes=(node, next_node), edges=(
                WorkflowEdge(run.id, 1, node.id, next_node.id),
            ))
            (old,) = unit.workflows.claim_ready(
                worker_id="original", lease_until=now + timedelta(seconds=30), limit=1,
            )
            unit.workflows.record_checkpoint(old, result=checkpoint)
            unit.workflows.request_pause(run.id)
            unit.workflows.defer(old, available_at=due, result=checkpoint)
            unit.workflows.create(other, nodes=(unrelated,), edges=())
            unit.workflows.request_pause(other.id)
            assert unit.workflows.next_wake_delay(now=now, maximum=5) == 5
            assert 1.9 < unit.workflows.next_wake_delay(
                now=now, maximum=5, reconciliation_phases=phases,
            ) < 2.1
            unit.commit()
    finally:
        engine.dispose()
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="left")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return due + timedelta(milliseconds=100)

    monkeypatch.setattr(repository, "datetime", Clock)
    kwargs = {"worker_id": "restarted", "lease_until": now + timedelta(seconds=30), "limit": 4}
    try:
        with SqlAlchemyUnitOfWorkFactory(engine, tenant_id="right")() as unit:
            assert not unit.workflows.claim_ready(**kwargs, reconciliation_phases=phases)
        with factory() as unit:
            if cancelled:
                unit.workflows.cancel(run.id)
            assert not unit.workflows.claim_ready(**kwargs)
            assert not unit.workflows.claim_ready(
                **kwargs, reconciliation_phases={node.kind: "another-phase"},
            )
            claims = unit.workflows.claim_ready(**kwargs, reconciliation_phases=phases)
            assert len(claims) == (0 if cancelled else 1)
            assert unit.workflows.get_run(other.id).status == "paused"
            assert unit.workflows.get_node(other.id, unrelated.id).attempt_count == 0
            assert unit.workflows.get_node(run.id, next_node.id).attempt_count == 0
            unit.commit()
        if claims:
            assert claims[0].node_id == node.id
            with pytest.raises(WorkflowFenceError), factory() as unit:
                unit.workflows.record_checkpoint(old, result=checkpoint)
            with factory() as unit:
                unit.workflows.complete(claims[0], result=checkpoint, evidence_refs=())
                assert unit.workflows.get_run(run.id).status == "paused"
                assert not unit.workflows.claim_ready(**kwargs, reconciliation_phases=phases)
                unit.commit()
    finally:
        engine.dispose()


def test_undeclared_adapters_have_no_paused_dispatch_authority():
    class Adapter:
        def paused_reconciliation_phase(self, kind):
            return "receipt-v1" if kind == "test.receipt" else None

    registry = WorkflowAdapterRegistry({
        "third-party": object(), "test.receipt": Adapter(), "test.write": Adapter(),
    })
    assert registry.reconciliation_phases() == {"test.receipt": "receipt-v1"}
