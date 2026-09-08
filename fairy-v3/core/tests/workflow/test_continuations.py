from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event, Lock

import pytest

from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.workflow.errors import WorkflowFenceError
from fairy_core.workflow.models import (
    WorkflowConcurrencyPolicy,
    WorkflowEdge,
    WorkflowNodeStatus,
    WorkflowRunStatus,
)
from fairy_core.workflow.scheduler import (
    WorkflowAdapterRegistry,
    WorkflowNodeResult,
    WorkflowScheduler,
)
from tests.workflow.test_repository import _node, _run


def _claim(factory, limit=4):
    with factory() as unit:
        result = unit.workflows.claim_ready(
            worker_id="continuation",
            lease_until=datetime.now(UTC) + timedelta(seconds=30),
            limit=limit,
        )
        unit.commit()
        return result


def test_completion_atomically_expands_parallel_graph_and_recovers_after_reopen(tmp_path):
    path = tmp_path / "continuation.db"
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="left")
    run = _run("continuation")
    model = _node(run, "model")
    left = _node(run, "read-left", policy=WorkflowConcurrencyPolicy.PARALLEL_READ)
    right = _node(run, "read-right", policy=WorkflowConcurrencyPolicy.PARALLEL_READ)
    join = _node(run, "join")
    edges = (WorkflowEdge(run.id, 1, left.id, join.id), WorkflowEdge(run.id, 1, right.id, join.id))
    with factory() as unit:
        unit.workflows.create(run, nodes=(model,), edges=())
        unit.commit()
    (claim,) = _claim(factory)
    try:
        with pytest.raises(RuntimeError, match="crash"), factory() as unit:
            unit.workflows.complete(
                claim,
                result={"calls": ["left", "right"]},
                evidence_refs=(),
                next_nodes=(left, right, join),
                next_edges=edges,
            )
            raise RuntimeError("crash before commit")
        with factory() as unit:
            before = unit.workflows.get(run.id)
            assert len(before.nodes) == 1
            assert before.nodes[0].status is WorkflowNodeStatus.RUNNING
            unit.workflows.complete(
                claim,
                result={"calls": ["left", "right"]},
                evidence_refs=(),
                next_nodes=(left, right, join),
                next_edges=edges,
            )
            unit.commit()
    finally:
        engine.dispose()

    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="left")
    try:
        with SqlAlchemyUnitOfWorkFactory(engine, tenant_id="right")() as unit:
            assert unit.workflows.get(run.id) is None
        with pytest.raises(WorkflowFenceError), factory() as unit:
            unit.workflows.complete(
                claim,
                result={},
                evidence_refs=(),
                next_nodes=(_node(run, "duplicate"),),
            )
        claims = _claim(factory)
        assert {item.node_id for item in claims} == {left.id, right.id}
        with factory() as unit:
            unit.workflows.complete(claims[1], result={"value": "second"}, evidence_refs=())
            unit.commit()
        assert _claim(factory) == ()
        with factory() as unit:
            unit.workflows.complete(claims[0], result={"value": "first"}, evidence_refs=())
            unit.commit()
        (joined,) = _claim(factory)
        assert joined.node_id == join.id
        with factory() as unit:
            snapshot = unit.workflows.complete(joined, result={"ordered": True}, evidence_refs=())
            unit.commit()
        assert snapshot.run.status is WorkflowRunStatus.COMPLETED
        assert len(snapshot.nodes) == 4
        assert len(snapshot.edges) == 4
        assert len(snapshot.revisions) == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "invalid",
    [
        "other-run",
        "revision",
        "cycle",
        "old-key",
        "old-edge",
        "duplicate-edges",
        "over-batch",
    ],
)
def test_continuation_rejects_scope_or_graph_changes_without_settling_attempt(tmp_path, invalid):
    engine = create_sqlite_core_engine(tmp_path / "invalid.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    run = _run("invalid")
    root = _node(run, "root")
    child = _node(run, "child")
    sibling = _node(run, "sibling")
    with factory() as unit:
        unit.workflows.create(run, nodes=(root,), edges=())
        unit.commit()
    (claim,) = _claim(factory)
    nodes, edges = (child,), ()
    if invalid == "other-run":
        nodes = (_node(_run("foreign"), "foreign"),)
    elif invalid == "revision":
        nodes = (replace(child, plan_revision=2),)
    elif invalid == "cycle":
        nodes = (child, sibling)
        edges = (
            WorkflowEdge(run.id, 1, child.id, sibling.id),
            WorkflowEdge(run.id, 1, sibling.id, child.id),
        )
    elif invalid == "old-key":
        nodes = (replace(child, node_key="root"),)
    elif invalid == "duplicate-edges":
        nodes = (child, sibling)
        edge = WorkflowEdge(run.id, 1, child.id, sibling.id)
        edges = (edge, edge)
    elif invalid == "over-batch":
        nodes = tuple(_node(run, f"extra-{index}") for index in range(129))
    else:
        edges = (WorkflowEdge(run.id, 1, child.id, root.id),)
    try:
        with factory() as unit:
            with pytest.raises(ValueError):
                unit.workflows.complete(
                    claim, result={}, evidence_refs=(), next_nodes=nodes, next_edges=edges
                )
            # Even a caller catching validation errors cannot commit half a completion.
            unit.commit()
        with factory() as unit:
            snapshot = unit.workflows.get(run.id)
        assert len(snapshot.nodes) == 1
        assert snapshot.nodes[0].status is WorkflowNodeStatus.RUNNING
    finally:
        engine.dispose()


@pytest.mark.parametrize("control", ["pause", "cancel"])
def test_continuation_obeys_control_boundary(tmp_path, control):
    engine = create_sqlite_core_engine(tmp_path / "control.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    run = _run(control)
    root, child = _node(run, "root"), _node(run, "child")
    with factory() as unit:
        unit.workflows.create(run, nodes=(root,), edges=())
        unit.commit()
    (claim,) = _claim(factory)
    try:
        with factory() as unit:
            if control == "pause":
                unit.workflows.request_pause(run.id)
            else:
                unit.workflows.cancel(run.id)
            unit.commit()
        if control == "cancel":
            with pytest.raises(WorkflowFenceError), factory() as unit:
                unit.workflows.complete(claim, result={}, evidence_refs=(), next_nodes=(child,))
        else:
            with factory() as unit:
                snapshot = unit.workflows.complete(
                    claim, result={}, evidence_refs=(), next_nodes=(child,)
                )
                unit.commit()
            assert snapshot.run.status is WorkflowRunStatus.PAUSED
        assert _claim(factory) == ()
        with factory() as unit:
            snapshot = unit.workflows.get(run.id)
        assert len(snapshot.nodes) == (2 if control == "pause" else 1)
    finally:
        engine.dispose()


def test_scheduler_expanded_reads_share_global_four_and_per_run_two_workers(tmp_path):
    engine = create_sqlite_core_engine(tmp_path / "scheduler.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    release, four_started = Event(), Event()
    lock = Lock()
    active, maxima = {}, {"global": 0, "per_run": 0}
    plans = {}
    runs = (_run("left"), _run("right"))
    for run in runs:
        root = _node(run, "model")
        reads = tuple(
            _node(run, f"read-{index}", policy=WorkflowConcurrencyPolicy.PARALLEL_READ)
            for index in range(3)
        )
        join = _node(run, "join")
        plans[root.id] = (
            (*reads, join),
            tuple(WorkflowEdge(run.id, 1, read.id, join.id) for read in reads),
        )
        with factory() as unit:
            unit.workflows.create(run, nodes=(root,), edges=())
            unit.commit()

    class Adapter:
        def execute(self, node, cancellation):
            if node.node_key == "model":
                nodes, edges = plans[node.id]
                return WorkflowNodeResult(output={"calls": 3}, next_nodes=nodes, next_edges=edges)
            if node.node_key.startswith("read-"):
                with lock:
                    active[node.run_id] = active.get(node.run_id, 0) + 1
                    maxima["global"] = max(maxima["global"], sum(active.values()))
                    maxima["per_run"] = max(maxima["per_run"], active[node.run_id])
                    if sum(active.values()) == 4:
                        four_started.set()
                try:
                    assert release.wait(5)
                    cancellation.raise_if_cancelled()
                finally:
                    with lock:
                        active[node.run_id] -= 1
            return WorkflowNodeResult(output={"value": node.node_key})

    scheduler = WorkflowScheduler(
        unit_of_work_factory=factory,
        adapters=WorkflowAdapterRegistry({"test.echo": Adapter()}),
        autostart=False,
    )
    try:
        scheduler.start()
        assert four_started.wait(4)
        assert maxima == {"global": 4, "per_run": 2}
        release.set()
        for run in runs:
            assert scheduler.wait(run.id, timeout=5).run.status is WorkflowRunStatus.COMPLETED
        assert maxima == {"global": 4, "per_run": 2}
    finally:
        release.set()
        scheduler.close()
        engine.dispose()
