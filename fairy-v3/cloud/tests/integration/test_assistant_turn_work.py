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
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


def test_postgres_turn_work_is_single_claim_fenced_and_tenant_scoped(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    tenant_a = postgres_test_context.track_tenant(f"turn-work-a-{uuid4().hex}")
    tenant_b = postgres_test_context.track_tenant(f"turn-work-b-{uuid4().hex}")
    engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    try:
        application_a, ledger_a = _stack(engine, tmp_path, tenant_a)
        _, ledger_b = _stack(engine, tmp_path, tenant_b)
        conversation = application_a.create_conversation(
            project_id=None,
            workspace_type=WorkspaceType.CHAT_SCRATCH,
        )
        task = application_a.create_task(
            TaskCreate(
                conversation_id=conversation.id,
                user_request="Claim durable assistant work once",
                operation_mode=OperationMode.ANSWER,
                execution_target=ExecutionTarget.CLOUD,
                idempotency_key="assistant:turn-work:task",
            )
        ).task
        turn = ledger_a.create_turn(
            task_id=task.id,
            profile_id="cloud-default",
            idempotency_key="assistant:turn-work:turn",
        )
        assert ledger_a.enqueue_turn_work(turn.id) == 1
        assert ledger_b.pending_turn_work_ids() == ()
        assert (
            ledger_b.claim_next_turn_work(
                worker_id="tenant-b-worker",
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
            )
            is None
        )

        barrier = Barrier(2)

        def claim(worker_id: str):
            barrier.wait(timeout=5)
            return ledger_a.claim_next_turn_work(
                worker_id=worker_id,
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = tuple(executor.map(claim, ("turn-worker-a", "turn-worker-b")))
        winners = tuple(claim for claim in claims if claim is not None)
        assert len(winners) == 1
        first_claim = winners[0]

        assert ledger_a.enqueue_turn_work(turn.id, force=True) == 2
        assert ledger_a.release_turn_work(first_claim)
        assert ledger_a.pending_turn_work_ids() == (turn.id,)

        second_claim = ledger_a.claim_next_turn_work(
            worker_id="turn-worker-c",
            lease_until=datetime.now(UTC) + timedelta(seconds=30),
        )
        assert second_claim is not None
        assert second_claim.request_revision == 2
        assert second_claim.lease_fence > first_claim.lease_fence
        assert not ledger_a.renew_turn_work(
            first_claim,
            lease_until=datetime.now(UTC) + timedelta(seconds=30),
        )
        assert ledger_a.release_turn_work(second_claim)
        assert ledger_a.pending_turn_work_ids() == ()

        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_b},
            )
            visible = connection.execute(
                text(
                    """
                    SELECT turn_id
                    FROM core_assistant_turn_work
                    WHERE turn_id = :turn_id
                    """
                ),
                {"turn_id": str(turn.id)},
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
    )
    return application, ledger
