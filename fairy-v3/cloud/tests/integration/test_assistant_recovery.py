from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fairy_core.application.core import CoreApplication
from fairy_core.assistant.ledger import AssistantLedgerApplication
from fairy_core.assistant.models import ToolInvocation, ToolInvocationStatus
from fairy_core.commanding.bus import CommandBus, CommandRequest
from fairy_core.commanding.models import EventVisibility
from fairy_core.commanding.policy import PermissionProfile, PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.commanding.schema import command_runs
from fairy_core.contracts.models import ExecutionTarget, TaskCreate
from fairy_core.domain.models import OperationMode, WorkspaceType
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from sqlalchemy import create_engine, text, update

from fairy_cloud.auth import RequestIdentity
from fairy_cloud.dispatchers import TenantRuntimeRegistry
from fairy_cloud.storage.postgres import tenant_id_for_user

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("crash_phase", ["model_started", "delta_persisted", "tool_dispatched"])
def test_postgres_recovers_each_durable_assistant_phase_without_duplicate_effects(
    crash_phase: str,
    tmp_path: Path,
    postgres_test_context,
) -> None:
    user_id = f"assistant-recovery-{crash_phase}-{uuid4().hex}"
    tenant_id = postgres_test_context.track_tenant(tenant_id_for_user(user_id))
    engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant_id)
    registry = build_default_registry()
    application = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(tmp_path / tenant_id),
        registry=registry,
        policy=PolicyEngine(registry),
    )
    ledger = AssistantLedgerApplication(
        unit_of_work_factory=factory,
        scope_resolver=application.scope_for_task,
    )
    runtimes: TenantRuntimeRegistry | None = None
    try:
        conversation = application.create_conversation(
            project_id=None,
            workspace_type=WorkspaceType.CHAT_SCRATCH,
        )
        task = application.create_task(
            TaskCreate(
                conversation_id=conversation.id,
                user_request=f"Recover {crash_phase}",
                operation_mode=OperationMode.ANSWER,
                execution_target=ExecutionTarget.CLOUD,
                idempotency_key=f"assistant:recovery:task:{crash_phase}",
            )
        ).task
        turn = ledger.create_turn(
            task_id=task.id,
            profile_id="cloud-default",
            idempotency_key=f"assistant:recovery:turn:{crash_phase}",
        )

        with factory() as unit_of_work:
            persisted = unit_of_work.assistant.get_turn(turn.id)
            assert persisted is not None
            previous_status = persisted.status
            persisted.start()
            if crash_phase == "tool_dispatched":
                persisted.wait_for_tool()
            unit_of_work.assistant.update_turn(
                persisted,
                expected_status=previous_status,
                expected_cancellation_revision=persisted.cancellation_revision,
            )
            scope = application.scope_for_task(
                unit_of_work.state, unit_of_work.state.get_task(task.id)
            )
            bus = CommandBus(
                registry=registry,
                policy=PolicyEngine(registry),
                ledger=unit_of_work.commands,
            )
            tool_name = "info.time" if crash_phase == "tool_dispatched" else "model.generate"
            payload = (
                {"timezone": "UTC"}
                if crash_phase == "tool_dispatched"
                else {"turn_id": str(turn.id), "profile_id": "cloud-default"}
            )
            dispatch = bus.submit(
                CommandRequest(
                    tool_name=tool_name,
                    actor="assistant",
                    scope=scope,
                    payload=payload,
                    idempotency_key=f"assistant:recovery:run:{crash_phase}",
                ),
                profile=PermissionProfile.STANDARD,
                capability_overrides={},
                sandbox_healthy=False,
            )
            assert dispatch.run is not None
            running = bus.start(
                dispatch.run.id,
                worker_id=f"crashed-worker:{crash_phase}",
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
            )
            if crash_phase == "delta_persisted":
                unit_of_work.commands.append_event(
                    run_id=running.id,
                    event_type="assistant.message.delta",
                    visibility=EventVisibility.USER,
                    message="Assistant response updated",
                    payload={
                        "turn_id": str(turn.id),
                        "model_round": 1,
                        "chunk_index": 1,
                        "text": "durable partial",
                    },
                    lease_owner=running.lease_owner,
                    lease_fence=running.lease_fence,
                )
            if crash_phase == "tool_dispatched":
                invocation = ToolInvocation.create(
                    turn=persisted,
                    model_round=1,
                    sequence=1,
                    provider_call_id="call-recovery-1",
                    tool_name=tool_name,
                    scope_digest=scope.scope_digest,
                    arguments=payload,
                )
                invocation.queue(command_run_id=running.id)
                invocation.start()
                unit_of_work.assistant.save_tool_invocation(invocation)
            unit_of_work.commit()

        with engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            connection.execute(
                update(command_runs)
                .where(
                    command_runs.c.tenant_id == tenant_id,
                    command_runs.c.id == str(running.id),
                )
                .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
            )

        runtimes = TenantRuntimeRegistry(root=tmp_path / "cloud", engine=engine)
        service = runtimes.for_identity(RequestIdentity(user_id, "device-a", frozenset()))
        recovered = service.invoke("assistant.turns.get", {"turn_id": str(turn.id)})
        replay = service.invoke("assistant.turns.run", {"turn_id": str(turn.id)})

        assert recovered["status"] == "failed"
        assert recovered["error_code"] == "WORKER_INTERRUPTED"
        assert replay == recovered
        with factory() as unit_of_work:
            messages = unit_of_work.assistant.list_messages(
                conversation_id=conversation.id,
                limit=100,
                cursor=None,
            ).items
            invocations = unit_of_work.assistant.list_tool_invocations(turn.id)
            recovered_run = unit_of_work.commands.get_run(running.id)
            events = unit_of_work.commands.events_for_run(running.id)
        assert len(messages) == 1
        assert recovered_run is not None
        assert recovered_run.status.value == "interrupted"
        assert [event.event_type for event in events].count("assistant.turn.failed") == 1
        assert [event.event_type for event in events].count("assistant.message.delta") == (
            1 if crash_phase == "delta_persisted" else 0
        )
        if crash_phase == "tool_dispatched":
            assert len(invocations) == 1
            assert invocations[0].status is ToolInvocationStatus.FAILED
            assert invocations[0].error_code == "WORKER_INTERRUPTED"
        else:
            assert invocations == ()
        _assert_recovery_event_and_outbox_are_atomic(engine, tenant_id, turn.id)
    finally:
        if runtimes is not None:
            runtimes.close()
        engine.dispose()


def _assert_recovery_event_and_outbox_are_atomic(engine, tenant_id: str, turn_id) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        count = connection.execute(
            text(
                """
                SELECT count(*)
                FROM domain_events AS events
                JOIN outbox
                  ON outbox.tenant_id = events.tenant_id
                 AND outbox.event_id = events.event_id
                WHERE events.tenant_id = :tenant_id
                  AND events.event_type = 'assistant.turn.failed'
                  AND events.payload ->> 'turn_id' = :turn_id
                """
            ),
            {"tenant_id": tenant_id, "turn_id": str(turn_id)},
        ).scalar_one()
    assert count == 1
