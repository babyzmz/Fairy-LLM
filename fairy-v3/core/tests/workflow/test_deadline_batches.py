from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from fairy_core.storage.schema import workflow_runs
from fairy_core.workflow.repository import SqlAlchemyWorkflowRepository
from tests.workflow.test_repository import _factory, _node, _run


def test_deadline_maintenance_is_bounded_without_dispatching_overdue_nodes(tmp_path):
    factory = _factory(tmp_path / "deadlines.db")
    overdue = [replace(_run(f"overdue:{index}"), created_at=datetime.now(UTC)
                       - timedelta(hours=3)) for index in range(65)]
    current = _run("not-overdue")
    with factory() as unit:
        for run in (*overdue, current):
            unit.workflows.create(run, nodes=(_node(run, "work"),), edges=())
        unit.commit()
    settled = []
    with factory() as unit:
        claims = unit.workflows.claim_ready(
            worker_id="first", lease_until=datetime.now(UTC) + timedelta(minutes=1),
            limit=4, on_failed=lambda run: settled.append(run.id),
        )
        assert len(settled) == 64
        assert len(claims) == 1 and claims[0].run_id == current.id
        unit.commit()
    with factory() as unit:
        assert not unit.workflows.claim_ready(
            worker_id="next", lease_until=datetime.now(UTC) + timedelta(minutes=1),
            limit=4, on_failed=lambda run: settled.append(run.id),
        )
        assert len(settled) == len(set(settled)) == 65
        for run in overdue:
            snapshot = unit.workflows.get(run.id)
            assert snapshot.run.status == "failed"
            assert snapshot.nodes[0].attempt_count == 0
        unit.commit()


def test_renewal_prioritizes_its_own_deadline_over_an_older_backlog(tmp_path):
    factory = _factory(tmp_path / "renewal.db")
    own = _run("renewing")
    with factory() as unit:
        unit.workflows.create(own, nodes=(_node(own, "running"),), edges=())
        (claim,) = unit.workflows.claim_ready(
            worker_id="held", lease_until=datetime.now(UTC) + timedelta(minutes=1), limit=1,
        )
        for index in range(65):
            older = replace(_run(f"older:{index}"), created_at=datetime.now(UTC)
                            - timedelta(hours=4))
            unit.workflows.create(older, nodes=(_node(older, "idle"),), edges=())
        unit._connection.execute(update(workflow_runs).where(
            workflow_runs.c.id == str(own.id), workflow_runs.c.tenant_id == "local",
        ).values(created_at=datetime.now(UTC) - timedelta(hours=3)))
        unit.commit()
    with factory() as unit:
        assert not unit.workflows.renew(
            claim, lease_until=datetime.now(UTC) + timedelta(minutes=1),
        )
        assert unit.workflows.get_run(own.id).status == "failed"


def test_skipped_locked_deadline_cannot_extend_an_expired_run(tmp_path, monkeypatch):
    factory = _factory(tmp_path / "locked.db")
    run = _run("locked-maintenance")
    with factory() as unit:
        unit.workflows.create(run, nodes=(_node(run, "running"),), edges=())
        (claim,) = unit.workflows.claim_ready(
            worker_id="held", lease_until=datetime.now(UTC) + timedelta(minutes=1), limit=1,
        )
        unit._connection.execute(update(workflow_runs).where(
            workflow_runs.c.id == str(run.id), workflow_runs.c.tenant_id == "local",
        ).values(created_at=datetime.now(UTC) - timedelta(hours=3)))
        unit.commit()
    # Simulate a deadline row unavailable to this maintenance batch (e.g. a PG
    # owner lock), not a real PostgreSQL concurrency acceptance test.
    monkeypatch.setattr(SqlAlchemyWorkflowRepository, "_expire_overdue", lambda *a, **k: None)
    with factory() as unit:
        assert not unit.workflows.renew(
            claim, lease_until=datetime.now(UTC) + timedelta(minutes=1),
        )
