from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fairy_core.contracts.common import ExecutionTarget
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.workflow.models import (
    WorkflowBudget,
    WorkflowConcurrencyPolicy,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeStatus,
    WorkflowPlanReason,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowTriggerKind,
)
from fairy_core.workflow.repository import (
    WorkflowBudgetExceeded,
    WorkflowFenceError,
    WorkflowRevisionError,
)


def _factory(path: Path, *, tenant_id: str = "local") -> SqlAlchemyUnitOfWorkFactory:
    return SqlAlchemyUnitOfWorkFactory(
        create_sqlite_core_engine(path, tenant_id=tenant_id),
        tenant_id=tenant_id,
    )


def _run(key: str, *, budget: WorkflowBudget | None = None) -> WorkflowRun:
    return WorkflowRun.create(
        owner_kind="test",
        owner_id=key,
        execution_target=ExecutionTarget.LOCAL,
        trigger_kind=WorkflowTriggerKind.MANUAL,
        idempotency_key=key,
        budget=budget,
    )


def _node(
    run: WorkflowRun,
    key: str,
    *,
    policy: WorkflowConcurrencyPolicy = WorkflowConcurrencyPolicy.SERIAL,
    resource_keys: tuple[str, ...] = (),
    max_attempts: int = 3,
) -> WorkflowNode:
    return WorkflowNode.create(
        run_id=run.id,
        plan_revision=1,
        node_key=key,
        kind="test.echo",
        payload={"value": key},
        public_summary=key,
        concurrency_policy=policy,
        resource_keys=resource_keys,
        max_attempts=max_attempts,
    )


def test_repository_executes_a_durable_dependency_graph(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "core.db")
    run = _run("graph")
    first = _node(run, "first")
    second = _node(run, "second")
    edge = WorkflowEdge(run.id, 1, first.id, second.id)

    with factory() as unit_of_work:
        snapshot = unit_of_work.workflows.create(run, nodes=(first, second), edges=(edge,))
        unit_of_work.commit()

    assert [node.status for node in snapshot.nodes] == [
        WorkflowNodeStatus.READY,
        WorkflowNodeStatus.PENDING,
    ]

    with factory() as unit_of_work:
        (claim,) = unit_of_work.workflows.claim_ready(
            worker_id="worker-a",
            lease_until=datetime.now(UTC) + timedelta(seconds=30),
            limit=4,
        )
        unit_of_work.commit()
    assert claim.node_id == first.id

    with factory() as unit_of_work:
        snapshot = unit_of_work.workflows.complete(
            claim,
            result={"value": "first"},
            evidence_refs=("evidence-1",),
        )
        unit_of_work.commit()
    assert next(node for node in snapshot.nodes if node.id == second.id).status is (
        WorkflowNodeStatus.READY
    )

    with factory() as unit_of_work:
        (claim,) = unit_of_work.workflows.claim_ready(
            worker_id="worker-b",
            lease_until=datetime.now(UTC) + timedelta(seconds=30),
            limit=1,
        )
        unit_of_work.commit()
    with factory() as unit_of_work:
        snapshot = unit_of_work.workflows.complete(
            claim,
            result={"value": "second"},
            evidence_refs=(),
        )
        unit_of_work.commit()

    assert snapshot.run.status is WorkflowRunStatus.COMPLETED
    assert len(snapshot.revisions) == 1


def test_expired_claim_is_fenced_and_recovered_once(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "core.db")
    run = _run("recovery")
    node = _node(run, "recover", max_attempts=3)
    with factory() as unit_of_work:
        unit_of_work.workflows.create(run, nodes=(node,), edges=())
        unit_of_work.commit()
    with factory() as unit_of_work:
        (old_claim,) = unit_of_work.workflows.claim_ready(
            worker_id="old-worker",
            lease_until=datetime.now(UTC) + timedelta(milliseconds=20),
            limit=1,
        )
        unit_of_work.commit()

    time.sleep(0.03)
    with factory() as unit_of_work:
        (new_claim,) = unit_of_work.workflows.claim_ready(
            worker_id="new-worker",
            lease_until=datetime.now(UTC) + timedelta(seconds=30),
            limit=1,
        )
        unit_of_work.commit()

    assert new_claim.attempt_number == 2
    with factory() as unit_of_work, pytest.raises(WorkflowFenceError):
        unit_of_work.workflows.complete(old_claim, result={}, evidence_refs=())
    with factory() as unit_of_work:
        snapshot = unit_of_work.workflows.complete(new_claim, result={}, evidence_refs=())
        unit_of_work.commit()
    assert snapshot.run.status is WorkflowRunStatus.COMPLETED


def test_parallel_reads_require_disjoint_resource_keys(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "core.db")
    budget = WorkflowBudget.normal()
    run = _run("parallel", budget=budget)
    left = _node(
        run,
        "left",
        policy=WorkflowConcurrencyPolicy.PARALLEL_READ,
        resource_keys=("source:left",),
    )
    right = _node(
        run,
        "right",
        policy=WorkflowConcurrencyPolicy.PARALLEL_READ,
        resource_keys=("source:right",),
    )
    blocked = _node(
        run,
        "blocked",
        policy=WorkflowConcurrencyPolicy.PARALLEL_READ,
        resource_keys=("source:left",),
    )
    with factory() as unit_of_work:
        unit_of_work.workflows.create(run, nodes=(left, right, blocked), edges=())
        unit_of_work.commit()

    with factory() as unit_of_work:
        claims = unit_of_work.workflows.claim_ready(
            worker_id="parallel-worker",
            lease_until=datetime.now(UTC) + timedelta(seconds=30),
            limit=4,
        )
        unit_of_work.commit()

    assert {claim.node_id for claim in claims} == {left.id, right.id}


@pytest.mark.parametrize("same_batch", [True, False])
def test_resource_keys_conflict_across_runs_but_not_unrelated_resources(
    tmp_path: Path,
    same_batch: bool,
) -> None:
    factory = _factory(tmp_path / "resources.db")
    first, blocked, independent = (_run(key) for key in ("first", "blocked", "independent"))
    nodes = [
        _node(
            run,
            run.owner_id,
            policy=WorkflowConcurrencyPolicy.PARALLEL_READ,
            resource_keys=(resource,),
        )
        for run, resource in (
            (first, "browser:shared"),
            (blocked, "browser:shared"),
            (independent, "browser:other"),
        )
    ]
    try:
        with factory() as unit:
            for run, node in zip((first, blocked, independent), nodes, strict=True):
                unit.workflows.create(run, nodes=(node,), edges=())
            unit.commit()
        with factory() as unit:
            initial = unit.workflows.claim_ready(
                worker_id="worker",
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
                limit=4 if same_batch else 1,
            )
            unit.commit()
        if same_batch:
            claims = initial
        else:
            with factory() as unit:
                claims = (
                    *initial,
                    *unit.workflows.claim_ready(
                        worker_id="worker",
                        lease_until=datetime.now(UTC) + timedelta(seconds=30),
                        limit=3,
                    ),
                )
                unit.commit()
        assert {claim.run_id for claim in claims} == {first.id, independent.id}
        with factory() as unit:
            for claim in claims:
                unit.workflows.complete(claim, result={}, evidence_refs=())
            unit.commit()
        with factory() as unit:
            remaining = unit.workflows.claim_ready(
                worker_id="worker",
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
                limit=4,
            )
            unit.commit()
        assert [claim.run_id for claim in remaining] == [blocked.id]
    finally:
        factory._engine.dispose()


def test_child_workflow_is_claimed_before_an_older_root_run(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "core.db")
    parent = _run("parent")
    parent_node = _node(parent, "parent")
    child = replace(_run("child"), parent_run_id=parent.id)
    child_node = _node(child, "child")
    with factory() as unit_of_work:
        unit_of_work.workflows.create(parent, nodes=(parent_node,), edges=())
        unit_of_work.workflows.create(child, nodes=(child_node,), edges=())
        unit_of_work.commit()

    with factory() as unit_of_work:
        (claim,) = unit_of_work.workflows.claim_ready(
            worker_id="child-first",
            lease_until=datetime.now(UTC) + timedelta(seconds=30),
            limit=1,
        )
        unit_of_work.commit()

    assert claim.run_id == child.id
    assert claim.node_id == child_node.id


def test_pause_cancel_and_tenant_isolation_are_durable(tmp_path: Path) -> None:
    path = tmp_path / "core.db"
    engine = create_sqlite_core_engine(path, tenant_id="tenant-a")
    local = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    other = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-b")
    run = _run("scope")
    node = _node(run, "node")
    with local() as unit_of_work:
        unit_of_work.workflows.create(run, nodes=(node,), edges=())
        unit_of_work.commit()
    with other() as unit_of_work:
        assert unit_of_work.workflows.get(run.id) is None

    with local() as unit_of_work:
        paused = unit_of_work.workflows.request_pause(run.id)
        unit_of_work.commit()
    assert paused.run.status is WorkflowRunStatus.PAUSED
    with local() as unit_of_work:
        resumed = unit_of_work.workflows.resume(run.id)
        unit_of_work.commit()
    assert resumed.run.status is WorkflowRunStatus.QUEUED
    with local() as unit_of_work:
        cancelled = unit_of_work.workflows.cancel(run.id)
        unit_of_work.commit()
    assert cancelled.run.status is WorkflowRunStatus.CANCELLED
    assert cancelled.run.cancellation_revision == 1


def test_instruction_is_idempotent_and_applies_one_immutable_plan_revision(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path / "core.db")
    run = _run("steering")
    old = _node(run, "old")
    with factory() as unit_of_work:
        unit_of_work.workflows.create(run, nodes=(old,), edges=())
        unit_of_work.commit()
    with factory() as unit_of_work:
        instruction = unit_of_work.workflows.add_instruction(
            run.id,
            instruction="Use the revised requirement",
            expected_revision=1,
            idempotency_key="steer-1",
        )
        replayed = unit_of_work.workflows.add_instruction(
            run.id,
            instruction="Use the revised requirement",
            expected_revision=1,
            idempotency_key="steer-1",
        )
        unit_of_work.commit()
    assert replayed == instruction

    revised = WorkflowNode.create(
        run_id=run.id,
        plan_revision=2,
        node_key="revised",
        kind="test.echo",
        payload={"value": "revised"},
        public_summary="Revised",
    )
    with factory() as unit_of_work:
        snapshot = unit_of_work.workflows.append_plan(
            run.id,
            expected_revision=1,
            reason=WorkflowPlanReason.STEERING,
            instruction_id=instruction.id,
            nodes=(revised,),
            edges=(),
        )
        unit_of_work.commit()

    assert snapshot.run.active_plan_revision == 2
    assert next(node for node in snapshot.nodes if node.id == old.id).status is (
        WorkflowNodeStatus.SUPERSEDED
    )
    assert snapshot.instructions[0].applied_revision == 2
    with factory() as unit_of_work, pytest.raises(WorkflowRevisionError):
        unit_of_work.workflows.append_plan(
            run.id,
            expected_revision=1,
            reason=WorkflowPlanReason.REPAIR,
            instruction_id=None,
            nodes=(revised,),
            edges=(),
        )


def test_repository_rejects_a_cyclic_plan(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "core.db")
    run = _run("cycle")
    first = _node(run, "first")
    second = _node(run, "second")
    edges = (
        WorkflowEdge(run.id, 1, first.id, second.id),
        WorkflowEdge(run.id, 1, second.id, first.id),
    )
    with factory() as unit_of_work, pytest.raises(ValueError, match="acyclic"):
        unit_of_work.workflows.create(run, nodes=(first, second), edges=edges)


def test_budget_reservations_are_durable_and_never_overdraw(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "core.db")
    run = _run("budget")
    node = _node(run, "node")
    with factory() as unit_of_work:
        unit_of_work.workflows.create(run, nodes=(node,), edges=())
        reserved = unit_of_work.workflows.reserve_budget(
            run.id,
            model_rounds=1,
            tool_invocations=2,
        )
        unit_of_work.commit()

    assert reserved.run.model_rounds_used == 1
    assert reserved.run.tool_invocations_used == 2
    with factory() as unit_of_work, pytest.raises(WorkflowBudgetExceeded):
        unit_of_work.workflows.reserve_budget(
            run.id,
            model_rounds=run.budget.max_model_rounds,
        )
    with factory() as unit_of_work:
        persisted = unit_of_work.workflows.get(run.id)
    assert persisted is not None
    assert persisted.run.model_rounds_used == 1


def test_workflow_budget_only_upgrades_from_normal_to_deep(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "core.db")
    run = _run("deep-budget")
    node = _node(run, "node")
    with factory() as unit_of_work:
        unit_of_work.workflows.create(run, nodes=(node,), edges=())
        upgraded = unit_of_work.workflows.upgrade_budget(
            run.id,
            budget=WorkflowBudget.deep(),
        )
        unit_of_work.commit()

    assert upgraded.run.budget == WorkflowBudget.deep()
    with (
        factory() as unit_of_work,
        pytest.raises(
            ValueError,
            match="only upgrade from normal to deep",
        ),
    ):
        unit_of_work.workflows.upgrade_budget(
            run.id,
            budget=WorkflowBudget.normal(),
        )


def test_overdue_workflow_fails_before_claiming_more_work(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "core.db")
    budget = WorkflowBudget(
        tier=WorkflowBudget.normal().tier,
        max_model_rounds=12,
        max_tool_invocations=32,
        max_duration_seconds=1,
        max_parallel_nodes=2,
    )
    now = datetime.now(UTC)
    run = replace(
        _run("deadline", budget=budget),
        created_at=now - timedelta(seconds=2),
        updated_at=now - timedelta(seconds=2),
    )
    node = _node(run, "node")
    with factory() as unit_of_work:
        unit_of_work.workflows.create(run, nodes=(node,), edges=())
        unit_of_work.commit()

    with factory() as unit_of_work:
        claims = unit_of_work.workflows.claim_ready(
            worker_id="late-worker",
            lease_until=now + timedelta(seconds=30),
            limit=1,
        )
        snapshot = unit_of_work.workflows.get(run.id)
        unit_of_work.commit()

    assert claims == ()
    assert snapshot is not None
    assert snapshot.run.status is WorkflowRunStatus.FAILED
    assert snapshot.run.error_code == "WORKFLOW_DEADLINE_EXCEEDED"
    assert snapshot.nodes[0].status is WorkflowNodeStatus.CANCELLED
