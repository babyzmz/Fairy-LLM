from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock, current_thread

from sqlalchemy import event

from fairy_core.contracts.common import ExecutionTarget
from fairy_core.domain.models import Project, ProjectResidency
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.workflow.models import WorkflowNode, WorkflowRun, WorkflowTriggerKind
from fairy_core.workflow.scheduler import (
    WorkflowAdapterRegistry,
    WorkflowNodeResult,
    WorkflowScheduler,
)


def _work(key: str) -> tuple[WorkflowRun, WorkflowNode]:
    run = WorkflowRun.create(
        owner_kind="test",
        owner_id=key,
        execution_target=ExecutionTarget.LOCAL,
        trigger_kind=WorkflowTriggerKind.MANUAL,
        idempotency_key=key,
    )
    node = WorkflowNode.create(
        run_id=run.id,
        plan_revision=1,
        node_key="node",
        kind="test.signal",
        payload={},
        public_summary="Signal test",
    )
    return run, node


def test_workflow_hint_only_follows_committed_workflow_writes(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "signals.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    other = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="other")
    local_event, other_event = Event(), Event()
    unsubscribe = factory.workflow_signal.subscribe(local_event)
    unsubscribe_other = other.workflow_signal.subscribe(other_event)
    run, node = _work("committed")
    try:
        with factory() as unit:
            unit.workflows.create(run, nodes=(node,), edges=())
            assert not local_event.is_set()
        assert not local_event.is_set()
        with factory() as unit:
            unit.workflows.create(run, nodes=(node,), edges=())
            unit.commit()
        assert local_event.is_set()
        assert not other_event.is_set()
        local_event.clear()
        with factory() as unit:
            unit.workflows.get(run.id)
            unit.workflows.create(run, nodes=(node,), edges=())  # idempotent replay
            unit.state.save_project(
                Project.create(
                    name="Unrelated state",
                    residency=ProjectResidency.LOCAL_ONLY,
                )
            )
            unit.commit()
        assert not local_event.is_set()
        with factory() as unit:
            unit.workflows.request_pause(run.id)
            unit.commit()
        assert local_event.is_set()
        unsubscribe()
        local_event.clear()
        with factory() as unit:
            unit.workflows.resume(run.id)
            unit.commit()
        assert not local_event.is_set()
    finally:
        unsubscribe()
        unsubscribe_other()
        engine.dispose()


class _SignallingAdapter:
    def __init__(self) -> None:
        self.started = Event()

    def execute(self, node, cancellation):
        cancellation.raise_if_cancelled()
        self.started.set()
        return WorkflowNodeResult(output={"done": True})


def test_idle_sql_drops_by_90_percent_without_delaying_committed_work(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "idle.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    adapter = _SignallingAdapter()
    count = 0
    lock = Lock()

    def count_selects(connection, cursor, statement, parameters, context, executemany):
        nonlocal count
        if (
            current_thread().name == "fairy-workflow-coordinator"
            and statement.lstrip().upper().startswith("SELECT")
        ):
            with lock:
                count += 1

    event.listen(engine, "after_cursor_execute", count_selects)
    scheduler = WorkflowScheduler(
        unit_of_work_factory=factory,
        adapters=WorkflowAdapterRegistry({"test.signal": adapter}),
    )
    try:
        time.sleep(6.6)  # Let the actual default coordinator reach its idle ceiling.
        with lock:
            count = 0
        time.sleep(3)
        with lock:
            idle_selects = count
        assert idle_selects <= 11, f"{idle_selects} SELECTs; prior 3s baseline was 112"
        run, node = _work("wake-from-idle")
        with factory() as unit:
            unit.workflows.create(run, nodes=(node,), edges=())
            unit.commit()
        dispatched_at = time.monotonic()
        assert adapter.started.wait(0.9), "Committed work must wake the idle coordinator"
        print(
            f"idle_3s_selects={idle_selects}; wake_seconds={time.monotonic() - dispatched_at:.3f}"
        )
        assert scheduler.wait(run.id, timeout=2).run.status.value == "completed"
    finally:
        scheduler.close()
        event.remove(engine, "after_cursor_execute", count_selects)
        engine.dispose()
    assert factory.workflow_signal.subscriber_count == 0


def test_next_wake_includes_deferred_nodes_without_loading_snapshots(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "due.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    run, node = _work("deferred")
    now = datetime.now(UTC)
    node = replace(node, available_at=now + timedelta(seconds=0.25))
    try:
        with factory() as unit:
            unit.workflows.create(run, nodes=(node,), edges=())
            unit.commit()
        with factory() as unit:
            delay = unit.workflows.next_wake_delay(now=now, maximum=5.0)
        assert 0.24 <= delay <= 0.26
    finally:
        engine.dispose()


def test_next_wake_tracks_leases_deadlines_and_tenant_isolation(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "maintenance-due.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    other = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="other")
    run, node = _work("lease")
    now = datetime.now(UTC)
    try:
        with factory() as unit:
            unit.workflows.create(run, nodes=(node,), edges=())
            unit.workflows.claim_ready(
                worker_id="other-process",
                lease_until=now + timedelta(seconds=2),
                limit=1,
            )
            unit.commit()
        with factory() as unit:
            assert 1.99 <= unit.workflows.next_wake_delay(now=now, maximum=5) <= 2.01
        with other() as unit:
            assert unit.workflows.next_wake_delay(now=now, maximum=5) == 5
        with factory() as unit:
            unit.workflows.cancel(run.id)
            unit.commit()
        urgent, urgent_node = _work("budget")
        urgent = replace(
            urgent,
            created_at=now
            - timedelta(
                seconds=urgent.budget.max_duration_seconds - 1,
            ),
        )
        urgent_node = replace(urgent_node, available_at=now + timedelta(seconds=10))
        with factory() as unit:
            unit.workflows.create(urgent, nodes=(urgent_node,), edges=())
            unit.commit()
        with factory() as unit:
            assert 0.99 <= unit.workflows.next_wake_delay(now=now, maximum=5) <= 1.01
    finally:
        engine.dispose()
