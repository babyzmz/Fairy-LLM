from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock

from fairy_core.contracts.common import ExecutionTarget
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.workflow.models import (
    WorkflowNode,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowTriggerKind,
)
from fairy_core.workflow.scheduler import (
    WorkflowAdapterRegistry,
    WorkflowNodeResult,
    WorkflowRetryableError,
    WorkflowScheduler,
    WorkflowWaitingForApproval,
)


class EchoAdapter:
    def execute(self, node, cancellation):
        cancellation.raise_if_cancelled()
        if node.attempt_count == 1 and node.payload.get("retry") is True:
            raise WorkflowRetryableError("retry", error_code="TRANSIENT")
        return WorkflowNodeResult(output={"value": node.payload["value"]})


class BlockingParentAdapter:
    may_wait_for_child_workflow = True

    def __init__(self) -> None:
        self.started: list[str] = []
        self.three_started = Event()
        self.release = Event()
        self.lock = Lock()

    def execute(self, node, cancellation):
        with self.lock:
            self.started.append(node.node_key)
            if len(self.started) == 3:
                self.three_started.set()
        while not self.release.wait(0.01):
            cancellation.raise_if_cancelled()
        return WorkflowNodeResult(output={"value": node.node_key})


def test_scheduler_reserves_one_worker_for_child_workflows(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    runs = []
    with factory() as unit_of_work:
        for index in range(4):
            run = WorkflowRun.create(
                owner_kind="test",
                owner_id=f"blocking-parent-{index}",
                execution_target=ExecutionTarget.LOCAL,
                trigger_kind=WorkflowTriggerKind.USER_TURN,
                idempotency_key=f"blocking-parent-{index}",
            )
            node = WorkflowNode.create(
                run_id=run.id,
                plan_revision=1,
                node_key=f"parent-{index}",
                kind="test.blocking-parent",
                payload={},
                public_summary="Potential child Workflow parent",
            )
            unit_of_work.workflows.create(run, nodes=(node,), edges=())
            runs.append(run)
        unit_of_work.commit()
    adapter = BlockingParentAdapter()
    scheduler = WorkflowScheduler(
        unit_of_work_factory=factory,
        adapters=WorkflowAdapterRegistry({"test.blocking-parent": adapter}),
        max_workers=4,
        poll_interval=0.01,
    )
    try:
        assert adapter.three_started.wait(5)
        time.sleep(0.05)
        assert len(adapter.started) == 3
        adapter.release.set()
        completed = tuple(scheduler.wait(run.id, timeout=5) for run in runs)
    finally:
        adapter.release.set()
        scheduler.close()

    assert all(snapshot.run.status.value == "completed" for snapshot in completed)
    assert len(adapter.started) == 4
    engine.dispose()


def test_scheduler_can_register_all_domain_adapters_before_start(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    run = WorkflowRun.create(
        owner_kind="test",
        owner_id="deferred-start",
        execution_target=ExecutionTarget.LOCAL,
        trigger_kind=WorkflowTriggerKind.DOMAIN,
        idempotency_key="deferred-start",
    )
    node = WorkflowNode.create(
        run_id=run.id,
        plan_revision=1,
        node_key="echo",
        kind="test.echo",
        payload={"value": "ok"},
        public_summary="Echo after composition",
    )
    with factory() as unit_of_work:
        unit_of_work.workflows.create(run, nodes=(node,), edges=())
        unit_of_work.commit()
    scheduler = WorkflowScheduler(
        unit_of_work_factory=factory,
        adapters=WorkflowAdapterRegistry({"test.echo": EchoAdapter()}),
        poll_interval=0.01,
        autostart=False,
    )
    try:
        time.sleep(0.03)
        with factory() as unit_of_work:
            before_start = unit_of_work.workflows.get(run.id)
        assert before_start is not None
        assert before_start.run.status.value == "queued"

        scheduler.start()
        completed = scheduler.wait(run.id, timeout=5)
        scheduler.start()
    finally:
        scheduler.close()

    assert completed.run.status.value == "completed"
    engine.dispose()


def test_scheduler_retries_and_completes_after_reopen(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    run = WorkflowRun.create(
        owner_kind="test",
        owner_id="scheduler",
        execution_target=ExecutionTarget.LOCAL,
        trigger_kind=WorkflowTriggerKind.MANUAL,
        idempotency_key="scheduler",
    )
    node = WorkflowNode.create(
        run_id=run.id,
        plan_revision=1,
        node_key="echo",
        kind="test.echo",
        payload={"value": "ok", "retry": True},
        public_summary="Echo",
        max_attempts=3,
    )
    with factory() as unit_of_work:
        unit_of_work.workflows.create(run, nodes=(node,), edges=())
        unit_of_work.commit()

    scheduler = WorkflowScheduler(
        unit_of_work_factory=factory,
        adapters=WorkflowAdapterRegistry({"test.echo": EchoAdapter()}),
        heartbeat_interval=0.02,
        poll_interval=0.01,
    )
    try:
        scheduler.wake()
        snapshot = scheduler.wait(run.id, timeout=5)
    finally:
        scheduler.close()

    assert snapshot.run.status.value == "completed"
    assert snapshot.nodes[0].attempt_count == 2
    engine.dispose()


class WaitingAdapter:
    def execute(self, node, cancellation):
        cancellation.raise_if_cancelled()
        if node.attempt_count == 1:
            return WorkflowNodeResult(
                output={"poll": 1},
                available_at=datetime.now(UTC) + timedelta(milliseconds=40),
            )
        return WorkflowNodeResult(output={"poll": 2})


def test_deferred_work_releases_the_worker_until_available(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    run = WorkflowRun.create(
        owner_kind="test",
        owner_id="wait",
        execution_target=ExecutionTarget.LOCAL,
        trigger_kind=WorkflowTriggerKind.DOMAIN,
        idempotency_key="wait",
    )
    node = WorkflowNode.create(
        run_id=run.id,
        plan_revision=1,
        node_key="wait",
        kind="test.wait",
        payload={},
        public_summary="Wait",
        max_attempts=3,
    )
    with factory() as unit_of_work:
        unit_of_work.workflows.create(run, nodes=(node,), edges=())
        unit_of_work.commit()
    scheduler = WorkflowScheduler(
        unit_of_work_factory=factory,
        adapters=WorkflowAdapterRegistry({"test.wait": WaitingAdapter()}),
        poll_interval=0.01,
    )
    started = time.monotonic()
    try:
        scheduler.wake()
        snapshot = scheduler.wait(run.id, timeout=5)
    finally:
        scheduler.close()

    assert snapshot.run.status.value == "completed"
    assert snapshot.nodes[0].attempt_count == 2
    assert time.monotonic() - started >= 0.03
    engine.dispose()


class ApprovalAdapter:
    def execute(self, node, cancellation):
        cancellation.raise_if_cancelled()
        if node.attempt_count == 1:
            raise WorkflowWaitingForApproval({"approval_id": "approval-1"})
        return WorkflowNodeResult(output={"approved": True})


class RacingApprovalAdapter:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def execute(self, node, cancellation):
        cancellation.raise_if_cancelled()
        if node.attempt_count == 1:
            self.started.set()
            assert self.release.wait(2)
            raise WorkflowWaitingForApproval({"approval_id": "approval-race"})
        return WorkflowNodeResult(output={"approved": True})


def test_waiting_for_approval_releases_the_lease_and_resumes(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    run = WorkflowRun.create(
        owner_kind="test",
        owner_id="approval",
        execution_target=ExecutionTarget.LOCAL,
        trigger_kind=WorkflowTriggerKind.MANUAL,
        idempotency_key="approval",
    )
    node = WorkflowNode.create(
        run_id=run.id,
        plan_revision=1,
        node_key="approval",
        kind="test.approval",
        payload={},
        public_summary="Approval",
        max_attempts=2,
    )
    with factory() as unit_of_work:
        unit_of_work.workflows.create(run, nodes=(node,), edges=())
        unit_of_work.commit()
    scheduler = WorkflowScheduler(
        unit_of_work_factory=factory,
        adapters=WorkflowAdapterRegistry({"test.approval": ApprovalAdapter()}),
        poll_interval=0.01,
    )
    try:
        scheduler.wake()
        waiting = scheduler.wait(run.id, timeout=5)
        assert waiting.run.status.value == "waiting_for_approval"
        assert waiting.nodes[0].result == {"approval_id": "approval-1"}
        scheduler.resume(run.id)
        completed = scheduler.wait(run.id, timeout=5)
    finally:
        scheduler.close()

    assert completed.run.status.value == "completed"
    assert completed.nodes[0].attempt_count == 2
    engine.dispose()


def test_approval_resume_requested_before_waiting_is_applied_at_boundary(
    tmp_path: Path,
) -> None:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    run = WorkflowRun.create(
        owner_kind="test",
        owner_id="approval-race",
        execution_target=ExecutionTarget.LOCAL,
        trigger_kind=WorkflowTriggerKind.MANUAL,
        idempotency_key="approval-race",
    )
    node = WorkflowNode.create(
        run_id=run.id,
        plan_revision=1,
        node_key="approval-race",
        kind="test.approval-race",
        payload={},
        public_summary="Approval race",
        max_attempts=2,
    )
    with factory() as unit_of_work:
        unit_of_work.workflows.create(run, nodes=(node,), edges=())
        unit_of_work.commit()
    adapter = RacingApprovalAdapter()
    scheduler = WorkflowScheduler(
        unit_of_work_factory=factory,
        adapters=WorkflowAdapterRegistry({"test.approval-race": adapter}),
        poll_interval=0.01,
    )
    try:
        scheduler.wake()
        assert adapter.started.wait(2)
        scheduler.resume_after_boundary(run.id)
        adapter.release.set()
        deadline = time.monotonic() + 5
        while True:
            with factory() as unit_of_work:
                completed = unit_of_work.workflows.get(run.id)
            assert completed is not None
            if completed.run.status is WorkflowRunStatus.COMPLETED:
                break
            if time.monotonic() >= deadline:
                raise AssertionError("Approval race Workflow did not resume")
            time.sleep(0.01)
    finally:
        adapter.release.set()
        scheduler.close()

    assert completed.run.status is WorkflowRunStatus.COMPLETED
    assert completed.nodes[0].attempt_count == 2
    engine.dispose()
