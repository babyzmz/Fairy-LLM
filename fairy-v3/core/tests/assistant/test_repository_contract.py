from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from fairy_core.assistant.ledger import AssistantLedgerApplication
from fairy_core.assistant.models import (
    AssistantTurn,
    AssistantTurnStatus,
    Message,
    MessageRole,
    MessageVisibility,
    ProviderAttempt,
    ToolInvocation,
)
from fairy_core.commanding.bus import CommandBus, CommandRequest
from fairy_core.commanding.policy import PermissionProfile, PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.commanding.schema import command_runs
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.models import (
    Conversation,
    OperationMode,
    Project,
    ProjectResidency,
    ScopeContract,
    Task,
    WorkspaceType,
)
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.providers import ProviderErrorCategory

_SNAPSHOT_HASH = "a" * 64


def _seed_task(factory: SqlAlchemyUnitOfWorkFactory, *, label: str) -> tuple[Task, ScopeContract]:
    project = Project.create(name=label, residency=ProjectResidency.LOCAL_ONLY)
    conversation = Conversation.create(
        project_id=project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=None,
    )
    task = Task.create(
        project_id=project.id,
        conversation_id=conversation.id,
        user_request=f"request {label}",
        operation_mode=OperationMode.ANSWER,
        base_version_id=None,
        execution_target="local",
    )
    task.bind_memory_snapshot(UUID(int=100), _SNAPSHOT_HASH)
    scope = ScopeContract.create(
        workspace_type=WorkspaceType.PROJECT_CHAT,
        project_id=project.id,
        conversation_id=conversation.id,
        task_id=task.id,
        operation_mode=task.operation_mode,
        base_version_id=None,
        target_version_id=None,
        project_root=Path("managed") / str(project.id),
        allowed_write_paths=(),
        forbidden_write_paths=(),
        execution_target="local",
        network_policy="open_web_safe",
        memory_read_scope=("current_conversation", "project_canonical"),
        memory_write_scope=("current_conversation_draft",),
        memory_snapshot_id=task.memory_snapshot_id,
        memory_snapshot_hash=task.memory_snapshot_hash,
    )
    with factory() as unit_of_work:
        unit_of_work.state.save_project(project)
        unit_of_work.state.save_conversation(conversation)
        unit_of_work.state.save_task(task, idempotency_key=f"task:{label}")
        unit_of_work.commit()
    return task, scope


def _turn(task: Task, scope: ScopeContract, *, key: str) -> AssistantTurn:
    return AssistantTurn.create(
        task=task,
        scope=scope,
        profile_id="local-default",
        idempotency_key=key,
    )


def test_repository_persists_turn_messages_and_tool_invocations(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "assistant.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    task, scope = _seed_task(factory, label="one")
    turn = _turn(task, scope, key="turn:one")
    user_message = Message.create(
        conversation_id=task.conversation_id,
        task_id=task.id,
        turn_id=turn.id,
        sequence=1,
        role=MessageRole.USER,
        visibility=MessageVisibility.USER,
        content="Hello",
    )
    assistant_message = Message.create(
        conversation_id=task.conversation_id,
        task_id=task.id,
        turn_id=turn.id,
        sequence=2,
        role=MessageRole.ASSISTANT,
        visibility=MessageVisibility.USER,
        content="Hi",
    )
    invocation = ToolInvocation.create(
        turn=turn,
        model_round=1,
        sequence=1,
        provider_call_id="call-time",
        tool_name="info.time",
        scope_digest=turn.scope_digest,
        arguments={"timezone": "Australia/Sydney"},
    )
    attempt = ProviderAttempt.create(
        turn=turn,
        model_round=1,
        attempt_number=1,
        profile_id="local-default",
    )

    with factory() as unit_of_work:
        unit_of_work.assistant.save_turn(turn)
        unit_of_work.assistant.append_message(user_message)
        unit_of_work.assistant.append_message(assistant_message)
        unit_of_work.assistant.save_tool_invocation(invocation)
        unit_of_work.assistant.save_provider_attempt(attempt)
        unit_of_work.commit()

    attempt.fail(error_category=ProviderErrorCategory.TIMEOUT, usage={"input_tokens": 5})
    with factory() as unit_of_work:
        unit_of_work.assistant.update_provider_attempt(attempt)
        unit_of_work.commit()

    with factory() as unit_of_work:
        restored = unit_of_work.assistant.get_turn(turn.id)
        first_page = unit_of_work.assistant.list_messages(
            conversation_id=task.conversation_id,
            limit=1,
            cursor=None,
        )
        second_page = unit_of_work.assistant.list_messages(
            conversation_id=task.conversation_id,
            limit=1,
            cursor=first_page.next_cursor,
        )
        invocations = unit_of_work.assistant.list_tool_invocations(turn.id)
        attempts = unit_of_work.assistant.list_provider_attempts(turn.id)

    assert restored == turn
    assert first_page.items == (user_message,)
    assert first_page.next_cursor is not None
    assert second_page.items == (assistant_message,)
    assert second_page.next_cursor is None
    assert invocations == (invocation,)
    assert attempts == (attempt,)


def test_repository_is_tenant_scoped_and_cursor_is_conversation_bound(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "tenants.db")
    tenant_a = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    tenant_b = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-b")
    task_a, scope_a = _seed_task(tenant_a, label="a")
    task_b, scope_b = _seed_task(tenant_b, label="b")
    turn_a = _turn(task_a, scope_a, key="same-key")
    turn_b = _turn(task_b, scope_b, key="same-key")
    message_a = Message.create(
        conversation_id=task_a.conversation_id,
        task_id=task_a.id,
        turn_id=turn_a.id,
        sequence=1,
        role=MessageRole.USER,
        visibility=MessageVisibility.USER,
        content="tenant a",
    )
    message_a2 = replace(message_a, id=UUID(int=999), sequence=2, content="tenant a 2")

    with tenant_a() as unit_of_work:
        unit_of_work.assistant.save_turn(turn_a)
        unit_of_work.assistant.append_message(message_a)
        unit_of_work.assistant.append_message(message_a2)
        unit_of_work.commit()
    with tenant_b() as unit_of_work:
        unit_of_work.assistant.save_turn(turn_b)
        unit_of_work.commit()

    with tenant_a() as unit_of_work:
        page = unit_of_work.assistant.list_messages(
            conversation_id=task_a.conversation_id,
            limit=1,
            cursor=None,
        )
        assert unit_of_work.assistant.find_turn_by_idempotency_key("same-key") == turn_a
    with tenant_b() as unit_of_work:
        assert unit_of_work.assistant.get_turn(turn_a.id) is None
        assert unit_of_work.assistant.find_turn_by_idempotency_key("same-key") == turn_b
        with pytest.raises(ValueError, match="cursor"):
            unit_of_work.assistant.list_messages(
                conversation_id=task_b.conversation_id,
                limit=1,
                cursor=page.next_cursor,
            )


def test_repository_constraints_reject_duplicate_sequences_and_keys(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "constraints.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    task, scope = _seed_task(factory, label="one")
    turn = _turn(task, scope, key="turn:key")
    duplicate_key = replace(_turn(task, scope, key="turn:key"), id=UUID(int=700))

    with factory() as unit_of_work:
        unit_of_work.assistant.save_turn(turn)
        unit_of_work.commit()
    with factory() as unit_of_work, pytest.raises(IntegrityError):
        unit_of_work.assistant.save_turn(duplicate_key)

    first = Message.create(
        conversation_id=task.conversation_id,
        task_id=task.id,
        turn_id=turn.id,
        sequence=1,
        role=MessageRole.USER,
        visibility=MessageVisibility.USER,
        content="one",
    )
    duplicate_sequence = replace(first, id=UUID(int=701), content="two")
    with factory() as unit_of_work:
        unit_of_work.assistant.append_message(first)
        unit_of_work.commit()
    with factory() as unit_of_work, pytest.raises(IntegrityError):
        unit_of_work.assistant.append_message(duplicate_sequence)

    invocation = ToolInvocation.create(
        turn=turn,
        model_round=1,
        sequence=1,
        provider_call_id="call-time",
        tool_name="info.time",
        scope_digest=turn.scope_digest,
        arguments={"timezone": "UTC"},
    )
    duplicate_invocation = replace(invocation, id=UUID(int=702))
    with factory() as unit_of_work:
        unit_of_work.assistant.save_tool_invocation(invocation)
        unit_of_work.commit()
    with factory() as unit_of_work, pytest.raises(IntegrityError):
        unit_of_work.assistant.save_tool_invocation(duplicate_invocation)


def test_repository_interrupts_only_orphaned_active_turns(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "recovery.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    task, scope = _seed_task(factory, label="one")
    live = _turn(task, scope, key="turn:live")
    orphaned = _turn(task, scope, key="turn:orphaned")
    live.start()
    orphaned.start()

    with factory() as unit_of_work:
        unit_of_work.assistant.save_turn(live)
        unit_of_work.assistant.save_turn(orphaned)
        unit_of_work.commit()
    with factory() as unit_of_work:
        interrupted = unit_of_work.assistant.interrupt_orphaned_turns(
            live_turn_ids=(live.id,),
        )
        unit_of_work.commit()

    with factory() as unit_of_work:
        restored_live = unit_of_work.assistant.get_turn(live.id)
        restored_orphaned = unit_of_work.assistant.get_turn(orphaned.id)

    assert tuple(turn.id for turn in interrupted) == (orphaned.id,)
    assert interrupted[0].status is AssistantTurnStatus.FAILED
    assert restored_live is not None
    assert restored_live.status is AssistantTurnStatus.RUNNING
    assert restored_orphaned is not None
    assert restored_orphaned.status is AssistantTurnStatus.FAILED
    assert restored_orphaned.error_code == "WORKER_INTERRUPTED"


def test_ledger_recovery_honors_live_command_lease_then_interrupts_expired_run(
    tmp_path: Path,
) -> None:
    engine = create_sqlite_core_engine(tmp_path / "lease-recovery.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    task, scope = _seed_task(factory, label="lease")
    turn = _turn(task, scope, key="turn:leased")
    turn.start()
    registry = build_default_registry()

    with factory() as unit_of_work:
        unit_of_work.assistant.save_turn(turn)
        bus = CommandBus(
            registry=registry,
            policy=PolicyEngine(registry),
            ledger=unit_of_work.commands,
        )
        dispatch = bus.submit(
            CommandRequest(
                tool_name="model.generate",
                actor="assistant",
                scope=scope,
                payload={"turn_id": str(turn.id), "profile_id": "local-default"},
                idempotency_key="assistant:leased:model:1",
            ),
            profile=PermissionProfile.STANDARD,
            capability_overrides={},
            sandbox_healthy=False,
        )
        assert dispatch.run is not None
        running = bus.start(
            dispatch.run.id,
            worker_id="core-live",
            lease_until=datetime.now(UTC) + timedelta(minutes=1),
        )
        unit_of_work.commit()

    recovery = AssistantLedgerApplication(
        unit_of_work_factory=factory,
        scope_resolver=lambda _state, _task: scope,
    )

    assert recovery.recover_orphaned_turns() == ()

    with engine.begin() as connection:
        connection.execute(
            update(command_runs)
            .where(command_runs.c.id == str(running.id))
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )

    assert tuple(item.id for item in recovery.recover_orphaned_turns()) == (turn.id,)
    with factory() as unit_of_work:
        recovered_turn = unit_of_work.assistant.get_turn(turn.id)
        recovered_run = unit_of_work.commands.get_run(running.id)
        events = unit_of_work.commands.events_for_run(running.id)

    assert recovered_turn is not None
    assert recovered_turn.status is AssistantTurnStatus.FAILED
    assert recovered_turn.error_code == "WORKER_INTERRUPTED"
    assert recovered_run is not None
    assert recovered_run.status.value == "interrupted"
    assert [event.event_type for event in events].count("assistant.turn.failed") == 1


def test_message_sequence_reservation_does_not_depend_on_existing_messages(
    tmp_path: Path,
) -> None:
    engine = create_sqlite_core_engine(tmp_path / "message-sequence.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    task, _ = _seed_task(factory, label="one")

    with factory() as unit_of_work:
        first = unit_of_work.assistant.next_message_sequence(task.conversation_id)
        unit_of_work.commit()
    with factory() as unit_of_work:
        second = unit_of_work.assistant.next_message_sequence(task.conversation_id)
        unit_of_work.commit()

    assert (first, second) == (1, 2)


def test_turn_creation_can_atomically_reuse_an_idempotent_winner(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "turn-create.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    task, scope = _seed_task(factory, label="one")
    winner = _turn(task, scope, key="turn:shared")
    contender = replace(_turn(task, scope, key="turn:shared"), id=UUID(int=703))

    with factory() as unit_of_work:
        persisted_winner, inserted_winner = unit_of_work.assistant.create_turn_if_absent(winner)
        unit_of_work.commit()
    with factory() as unit_of_work:
        persisted_contender, inserted_contender = unit_of_work.assistant.create_turn_if_absent(
            contender
        )
        unit_of_work.commit()

    assert inserted_winner is True
    assert persisted_winner.id == winner.id
    assert inserted_contender is False
    assert persisted_contender.id == winner.id


def test_turn_update_is_status_and_cancellation_revision_fenced(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "turn-fence.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    task, scope = _seed_task(factory, label="one")
    turn = _turn(task, scope, key="turn:fenced")
    with factory() as unit_of_work:
        unit_of_work.assistant.save_turn(turn)
        unit_of_work.commit()

    first = replace(turn)
    stale = replace(turn)
    first.cancel()
    stale.cancel()
    with factory() as unit_of_work:
        unit_of_work.assistant.update_turn(
            first,
            expected_status=AssistantTurnStatus.CREATED,
            expected_cancellation_revision=0,
        )
        unit_of_work.commit()
    with factory() as unit_of_work, pytest.raises(InvalidTransitionError):
        unit_of_work.assistant.update_turn(
            stale,
            expected_status=AssistantTurnStatus.CREATED,
            expected_cancellation_revision=0,
        )


def test_public_message_listing_excludes_internal_messages(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "message-visibility.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    task, scope = _seed_task(factory, label="one")
    turn = _turn(task, scope, key="turn:visibility")
    public = Message.create(
        conversation_id=task.conversation_id,
        task_id=task.id,
        turn_id=turn.id,
        sequence=1,
        role=MessageRole.USER,
        visibility=MessageVisibility.USER,
        content="visible",
    )
    internal = Message.create(
        conversation_id=task.conversation_id,
        task_id=task.id,
        turn_id=turn.id,
        sequence=2,
        role=MessageRole.SYSTEM_NOTICE,
        visibility=MessageVisibility.INTERNAL,
        content="provider prompt must remain hidden",
    )
    with factory() as unit_of_work:
        unit_of_work.assistant.save_turn(turn)
        unit_of_work.assistant.append_message(public)
        unit_of_work.assistant.append_message(internal)
        unit_of_work.commit()
    application = AssistantLedgerApplication(
        unit_of_work_factory=factory,
        scope_resolver=lambda _state, _task: scope,
    )

    page = application.list_messages(
        conversation_id=task.conversation_id,
        limit=100,
        cursor=None,
    )

    assert page.items == (public,)


def test_tool_invocation_first_write_cannot_upsert_immutable_binding(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "tool-immutable.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    task, scope = _seed_task(factory, label="one")
    turn = _turn(task, scope, key="turn:tool-immutable")
    invocation = ToolInvocation.create(
        turn=turn,
        model_round=1,
        sequence=1,
        provider_call_id="call-time",
        tool_name="info.time",
        scope_digest=turn.scope_digest,
        arguments={"timezone": "UTC"},
    )
    forged = replace(
        invocation,
        tool_name="system.open_url",
        argument_hash="b" * 64,
        arguments={"url": "https://example.invalid"},
    )
    with factory() as unit_of_work:
        unit_of_work.assistant.save_turn(turn)
        unit_of_work.assistant.save_tool_invocation(invocation)
        unit_of_work.commit()

    with factory() as unit_of_work, pytest.raises(IntegrityError):
        unit_of_work.assistant.save_tool_invocation(forged)
