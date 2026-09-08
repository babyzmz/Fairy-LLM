from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import update

from fairy_core.providers import CancellationToken, ProviderRegistry
from fairy_core.storage.schema import workflow_attempts, workflow_nodes
from fairy_core.transports.stdio import build_local_service
from fairy_core.workflow.scheduler import (
    WorkflowNodeResult,
    WorkflowPaused,
    WorkflowRetryableError,
    _ActiveNode,
)
from tests.assistant.test_application import _scratch_task, _turn
from tests.assistant.test_step_workflow import _provider


class _TerminalAdapter:
    def __init__(self, outcome):
        self.outcome = outcome

    def execute(self, node, cancellation):
        if self.outcome == "retry":
            raise WorkflowRetryableError("Injected exhausted retry", error_code="RETRY_EXHAUSTED")
        if self.outcome == "defer":
            return WorkflowNodeResult(
                output={"pending": True}, available_at=datetime.now(UTC) + timedelta(seconds=1),
            )
        if self.outcome == "abandon":
            raise WorkflowPaused()
        raise RuntimeError("Injected adapter failure")


@pytest.mark.parametrize("engine_version", [3, 4])
@pytest.mark.parametrize("outcome,error_code", [
    ("retry", "RETRY_EXHAUSTED"), ("defer", "WORKFLOW_WAIT_BUDGET_EXHAUSTED"),
    ("abandon", "WORKER_INTERRUPTED"), ("exception", "WORKFLOW_NODE_FAILED"),
    ("expired", "WORKER_LEASE_EXPIRED"),
])
def test_terminal_node_outcome_settles_its_domain_without_restart(
    tmp_path, engine_version, outcome, error_code,
):
    provider = _provider()
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((provider,)),
        assistant_workflow_engine_version=engine_version,
    )
    kernel = service._workflow_scheduler
    kernel.close()
    try:
        task = _scratch_task(service, "Finish this failed task immediately")
        turn = _turn(service, task, "failed-node")
        other_task = _scratch_task(service, "Keep this other task unchanged")
        other = _turn(service, other_task, "untouched-node")
        run_id = UUID(turn["workflow_run_id"])
        with service._unit_of_work_factory() as unit:
            unit._connection.execute(update(workflow_nodes).where(
                workflow_nodes.c.run_id == str(run_id), workflow_nodes.c.tenant_id == "local",
            ).values(max_attempts=1))
            unit.workflows.resume(run_id)
            (claim,) = unit.workflows.claim_ready(
                worker_id="terminal-test", lease_until=datetime.now(UTC) + timedelta(minutes=1),
                limit=1,
            )
            node = unit.workflows.get_node(run_id, claim.node_id)
            unit.commit()
        if outcome == "expired":
            with service._unit_of_work_factory() as unit:
                unit._connection.execute(update(workflow_attempts).where(
                    workflow_attempts.c.run_id == str(run_id),
                    workflow_attempts.c.tenant_id == "local",
                ).values(lease_until=datetime.now(UTC) - timedelta(seconds=1)))
                unit.commit()
            kernel._claim_available()
        else:
            kernel._adapters._adapters[node.kind] = _TerminalAdapter(outcome)
            kernel._execute(_ActiveNode(claim, CancellationToken(), node.kind, None, node))
        with service._unit_of_work_factory() as unit:
            snapshot = unit.workflows.get(run_id)
            projected = unit.assistant.get_turn(UUID(turn["id"]))
            assert snapshot.run.status == projected.status == "failed"
            assert projected.error_code == error_code
            assert unit.state.get_task(UUID(task["id"])).status == "failed"
            assert all(node.status in {"failed", "cancelled"} for node in snapshot.nodes)
            assert snapshot.run.cancellation_revision == 1
            assert unit.assistant.get_turn(UUID(other["id"])).status == other["status"]
            assert unit.state.get_task(UUID(other_task["id"])).status == "planning"
        assert not provider.requests
    finally:
        service.close()


@pytest.mark.parametrize("expired", [False, True])
def test_terminal_projection_failure_rolls_back_the_attempt(tmp_path, monkeypatch, expired):
    service = build_local_service(tmp_path, assistant_workflow_engine_version=4)
    kernel = service._workflow_scheduler
    kernel.close()
    try:
        task = _scratch_task(service, "Atomic terminal projection")
        turn = _turn(service, task, "atomic-terminal")
        run_id = UUID(turn["workflow_run_id"])
        with service._unit_of_work_factory() as unit:
            unit._connection.execute(update(workflow_nodes).where(
                workflow_nodes.c.run_id == str(run_id),
            ).values(max_attempts=1))
            unit.workflows.resume(run_id)
            (claim,) = unit.workflows.claim_ready(
                worker_id="atomic", lease_until=datetime.now(UTC) + timedelta(minutes=1), limit=1,
            )
            if expired:
                unit._connection.execute(update(workflow_attempts).where(
                    workflow_attempts.c.run_id == str(run_id),
                ).values(lease_until=datetime.now(UTC) - timedelta(seconds=1)))
            unit.commit()

        def unavailable_projection(unit, run):
            raise RuntimeError("Injected projection rollback")

        with monkeypatch.context() as patch:
            patch.setattr(kernel._adapters, "settle_failed_run", unavailable_projection)
            if expired:
                with pytest.raises(RuntimeError, match="projection rollback"):
                    kernel._claim_available()
            else:
                assert not kernel._abandon(claim)
        with service._unit_of_work_factory() as unit:
            assert unit.workflows.get_run(run_id).status == "running"
            assert unit.workflows.get_node(run_id, claim.node_id).status == "running"
            assert unit.assistant.get_turn(UUID(turn["id"])).status == "created"
        if expired:
            kernel._claim_available()
        else:
            assert kernel._abandon(claim)
        with service._unit_of_work_factory() as unit:
            assert unit.workflows.get_run(run_id).status == "failed"
            assert unit.assistant.get_turn(UUID(turn["id"])).status == "failed"
    finally:
        service.close()
