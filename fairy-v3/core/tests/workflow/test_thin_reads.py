from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

from fairy_core.workflow import repository_records
from fairy_core.workflow.models import WorkflowBudget, WorkflowEdge
from fairy_core.workflow.scheduler import (
    WorkflowAdapterRegistry,
    WorkflowNodeResult,
    WorkflowScheduler,
)
from tests.workflow.test_repository import _factory, _node, _run


def test_claim_heartbeat_and_wait_do_not_reload_old_node_result_bodies(tmp_path, monkeypatch):
    factory = _factory(tmp_path / "thin.db")
    run = _run("large-history")
    history, work = _node(run, "history"), _node(run, "work")
    with factory() as unit:
        unit.workflows.create(
            run,
            nodes=(history, work),
            edges=(WorkflowEdge(run.id, 1, history.id, work.id),),
        )
        (claim,) = unit.workflows.claim_ready(
            worker_id="seed",
            lease_until=datetime.now(UTC) + timedelta(seconds=30),
            limit=1,
        )
        unit.workflows.complete(claim, result={"large_history": "x" * 1_000_000}, evidence_refs=())
        unit.commit()
    loaded_bytes = []
    original = repository_records.node_from_row

    def counted(row):
        value = row["result"]
        if value and "large_history" in value:
            loaded_bytes.append(len(value["large_history"]))
        return original(row)

    monkeypatch.setattr(repository_records, "node_from_row", counted)
    with factory() as unit:
        reserved = unit.workflows.reserve_budget_run(run.id, model_rounds=1, tool_invocations=1)
        assert reserved.model_rounds_used == reserved.tool_invocations_used == 1
        upgraded = unit.workflows.upgrade_budget_run(run.id, budget=WorkflowBudget.deep())
        assert upgraded.budget == WorkflowBudget.deep()
        unit.commit()
    assert loaded_bytes == []
    started, release, heartbeats = Event(), Event(), Event()

    class Adapter:
        beats = 0

        def execute(self, node, cancellation):
            started.set()
            assert release.wait(5)
            return WorkflowNodeResult(output={"done": True})

        def heartbeat(self, node):
            self.beats += 1
            if self.beats >= 3:
                heartbeats.set()
            return True

    scheduler = WorkflowScheduler(
        unit_of_work_factory=factory,
        adapters=WorkflowAdapterRegistry({"test.echo": Adapter()}),
        heartbeat_interval=0.05,
        poll_interval=0.01,
    )
    try:
        assert started.wait(2)
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting = pool.submit(scheduler.wait, run.id, timeout=4)
            try:
                assert heartbeats.wait(2)
                assert sum(loaded_bytes) == 0, "Old model bodies must not be polling payloads"
            finally:
                release.set()
            assert waiting.result(timeout=2).run.status == "completed"
    finally:
        release.set()
        scheduler.close()


def test_thin_reads_preserve_tenant_and_run_isolation(tmp_path):
    path = tmp_path / "scopes.db"
    factory = _factory(path)
    first, second = _run("first"), _run("second")
    first_node, second_node = _node(first, "first"), _node(second, "second")
    with factory() as unit:
        unit.workflows.create(first, nodes=(first_node,), edges=())
        unit.workflows.create(second, nodes=(second_node,), edges=())
        unit.commit()
    with factory() as unit:
        assert unit.workflows.get_run(first.id).id == first.id
        assert unit.workflows.get_node(first.id, first_node.id).id == first_node.id
        assert unit.workflows.get_node(first.id, second_node.id) is None
    with _factory(path, tenant_id="other")() as unit:
        assert unit.workflows.get_run(first.id) is None
        assert unit.workflows.get_node(first.id, first_node.id) is None
