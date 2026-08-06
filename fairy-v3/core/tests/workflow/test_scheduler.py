from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fairy_core.contracts.common import ExecutionTarget
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.workflow.models import WorkflowNode, WorkflowRun, WorkflowTriggerKind
from fairy_core.workflow.scheduler import (
    WorkflowAdapterRegistry,
    WorkflowNodeResult,
    WorkflowRetryableError,
    WorkflowScheduler,
)


class EchoAdapter:
    def execute(self, node, cancellation):
        cancellation.raise_if_cancelled()
        if node.attempt_count == 1 and node.payload.get("retry") is True:
            raise WorkflowRetryableError("retry", error_code="TRANSIENT")
        return WorkflowNodeResult(output={"value": node.payload["value"]})


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
