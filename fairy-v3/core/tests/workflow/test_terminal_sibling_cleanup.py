from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from fairy_core.storage.schema import workflow_attempts
from fairy_core.workflow.errors import WorkflowFenceError
from fairy_core.workflow.models import WorkflowConcurrencyPolicy
from tests.workflow.test_repository import _factory, _node, _run


@pytest.mark.parametrize("mode", ["abandon", "expired"])
def test_exhausted_attempt_invalidates_siblings_and_preserves_another_run(tmp_path, mode):
    factory = _factory(tmp_path / "siblings.db")
    run, other = _run("failed-parallel"), _run("unrelated")
    nodes = tuple(_node(
        run, str(index), max_attempts=1, policy=WorkflowConcurrencyPolicy.PARALLEL_READ,
    ) for index in range(2))
    try:
        with factory() as unit:
            unit.workflows.create(run, nodes=nodes, edges=())
            unit.workflows.create(other, nodes=(_node(other, "untouched"),), edges=())
            unit.workflows.request_pause(other.id)
            claims = unit.workflows.claim_ready(
                worker_id="parallel", lease_until=datetime.now(UTC) + timedelta(minutes=1),
                limit=2,
            )
            unit.commit()
        assert len(claims) == 2
        notifications = []
        with factory() as unit:
            if mode == "abandon":
                assert unit.workflows.abandon(claims[0])
            else:
                unit._connection.execute(update(workflow_attempts).where(
                    workflow_attempts.c.run_id == str(run.id),
                ).values(lease_until=datetime.now(UTC) - timedelta(seconds=1)))
                assert not unit.workflows.claim_ready(
                    worker_id="recover", lease_until=datetime.now(UTC) + timedelta(minutes=1),
                    limit=2, on_failed=lambda failed: notifications.append(failed.id),
                )
                assert notifications == [run.id]
            unit.commit()
        with factory() as unit:
            snapshot = unit.workflows.get(run.id)
            assert snapshot.run.status == "failed"
            assert snapshot.run.cancellation_revision == 1
            assert sorted(node.status for node in snapshot.nodes) == ["cancelled", "failed"]
            attempts = unit._connection.execute(select(workflow_attempts).where(
                workflow_attempts.c.run_id == str(run.id),
            )).mappings().all()
            assert all(item["status"] != "running" and item["lease_owner"] is None
                       for item in attempts)
            assert unit.workflows.get_run(other.id).status == "paused"
            with pytest.raises(WorkflowFenceError):
                unit.workflows.complete(claims[1], result={}, evidence_refs=())
    finally:
        factory._engine.dispose()
