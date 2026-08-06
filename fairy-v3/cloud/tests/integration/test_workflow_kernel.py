from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from fairy_core.application.core import CoreApplication
from fairy_core.assistant.ledger import AssistantLedgerApplication
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.models import ExecutionTarget, TaskCreate
from fairy_core.domain.models import OperationMode, WorkspaceType
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.workflow.errors import WorkflowFenceError
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


def test_postgres_workflow_claim_is_fenced_and_tenant_scoped(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    tenant_a = postgres_test_context.track_tenant(f"workflow-a-{uuid4().hex}")
    tenant_b = postgres_test_context.track_tenant(f"workflow-b-{uuid4().hex}")
    engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    try:
        application_a, ledger_a, factory_a = _stack(engine, tmp_path, tenant_a)
        _application_b, _ledger_b, factory_b = _stack(engine, tmp_path, tenant_b)
        conversation = application_a.create_conversation(
            project_id=None,
            workspace_type=WorkspaceType.CHAT_SCRATCH,
        )
        task = application_a.create_task(
            TaskCreate(
                conversation_id=conversation.id,
                user_request="Claim durable Workflow work once",
                operation_mode=OperationMode.ANSWER,
                execution_target=ExecutionTarget.CLOUD,
                idempotency_key="workflow:task",
            )
        ).task
        turn = ledger_a.create_turn(
            task_id=task.id,
            profile_id="cloud-default",
            idempotency_key="workflow:turn",
        )
        assert turn.workflow_run_id is not None
        run_id = turn.workflow_run_id
        with factory_a() as unit_of_work:
            unit_of_work.workflows.resume(run_id)
            unit_of_work.commit()
        with factory_b() as unit_of_work:
            assert unit_of_work.workflows.get(run_id) is None

        barrier = Barrier(2)

        def claim(worker_id: str):
            barrier.wait(timeout=5)
            with factory_a() as unit_of_work:
                result = unit_of_work.workflows.claim_ready(
                    worker_id=worker_id,
                    lease_until=datetime.now(UTC) + timedelta(seconds=30),
                    limit=1,
                )
                unit_of_work.commit()
                return result

        with ThreadPoolExecutor(max_workers=2) as executor:
            claim_sets = tuple(executor.map(claim, ("workflow-a", "workflow-b")))
        winners = tuple(claim for claims in claim_sets for claim in claims)
        assert len(winners) == 1
        first_claim = winners[0]

        with engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_a},
            )
            connection.execute(
                text(
                    """
                    UPDATE core_workflow_attempts
                    SET lease_until = :expired
                    WHERE node_id = :node_id AND attempt_number = :attempt_number
                    """
                ),
                {
                    "expired": datetime.now(UTC) - timedelta(seconds=1),
                    "node_id": str(first_claim.node_id),
                    "attempt_number": first_claim.attempt_number,
                },
            )
        with factory_a() as unit_of_work:
            (second_claim,) = unit_of_work.workflows.claim_ready(
                worker_id="workflow-after-restart",
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
                limit=1,
            )
            assert second_claim.lease_fence > first_claim.lease_fence
            with pytest.raises(WorkflowFenceError):
                unit_of_work.workflows.complete(first_claim, result={}, evidence_refs=())

        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_b},
            )
            visible = connection.execute(
                text("SELECT id FROM core_workflow_runs WHERE id = :run_id"),
                {"run_id": str(run_id)},
            ).scalar_one_or_none()
            transaction.rollback()
        assert visible is None
    finally:
        engine.dispose()


def _stack(engine, root: Path, tenant_id: str):
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant_id)
    registry = build_default_registry()
    application = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(root / tenant_id),
        registry=registry,
        policy=PolicyEngine(registry),
    )
    ledger = AssistantLedgerApplication(
        unit_of_work_factory=factory,
        scope_resolver=application.scope_for_task,
        registry=registry,
        execution_target=ExecutionTarget.CLOUD,
    )
    return application, ledger, factory
