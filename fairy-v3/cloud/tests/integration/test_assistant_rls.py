from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fairy_core.application.core import CoreApplication
from fairy_core.assistant.ledger import AssistantLedgerApplication
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.models import ExecutionTarget, TaskCreate
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.models import OperationMode, WorkspaceType
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


def test_postgres_assistant_ledger_is_tenant_scoped_idempotent_and_revision_fenced(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    tenant_a = postgres_test_context.track_tenant(f"assistant-a-{uuid4().hex}")
    tenant_b = postgres_test_context.track_tenant(f"assistant-b-{uuid4().hex}")
    engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    try:
        application_a, ledger_a, factory_a = _stack(engine, tmp_path, tenant_a)
        _, _ledger_b, factory_b = _stack(engine, tmp_path, tenant_b)
        conversation = application_a.create_conversation(
            project_id=None,
            workspace_type=WorkspaceType.CHAT_SCRATCH,
        )
        task = application_a.create_task(
            TaskCreate(
                conversation_id=conversation.id,
                user_request="Tenant scoped turn",
                operation_mode=OperationMode.ANSWER,
                execution_target=ExecutionTarget.CLOUD,
                idempotency_key="assistant:task",
            )
        ).task
        created = ledger_a.create_turn(
            task_id=task.id,
            profile_id="cloud-default",
            idempotency_key="assistant:turn",
        )
        replayed = ledger_a.create_turn(
            task_id=task.id,
            profile_id="cloud-default",
            idempotency_key="assistant:turn",
        )

        assert replayed.id == created.id
        with factory_a() as unit_of_work:
            assert (
                len(
                    unit_of_work.assistant.list_messages(
                        conversation_id=conversation.id,
                        limit=100,
                        cursor=None,
                    ).items
                )
                == 1
            )
        with factory_b() as unit_of_work:
            assert unit_of_work.assistant.get_turn(created.id) is None
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_b},
            )
            assert (
                connection.execute(
                    text("SELECT id FROM core_assistant_turns WHERE id = :turn_id"),
                    {"turn_id": str(created.id)},
                ).scalar_one_or_none()
                is None
            )
            transaction.rollback()

        stale_revision = created.cancellation_revision
        cancelled = ledger_a.cancel_turn(
            turn_id=created.id,
            expected_cancellation_revision=stale_revision,
        )
        assert cancelled.cancellation_revision == stale_revision + 1
        with pytest.raises(InvalidTransitionError):
            ledger_a.cancel_turn(
                turn_id=created.id,
                expected_cancellation_revision=stale_revision,
            )
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
    return application, ledger, factory
