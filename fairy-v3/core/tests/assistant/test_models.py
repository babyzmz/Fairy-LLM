from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from fairy_core.assistant.models import (
    AssistantTurn,
    AssistantTurnStatus,
    Message,
    MessageRole,
    MessageVisibility,
    ToolInvocation,
    ToolInvocationStatus,
)
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.models import (
    OperationMode,
    ScopeContract,
    Task,
    WorkspaceType,
)

_SNAPSHOT_HASH = "a" * 64


def _task() -> Task:
    task = Task.create(
        project_id=None,
        conversation_id=UUID(int=1),
        user_request="Explain the project",
        operation_mode=OperationMode.ANSWER,
        base_version_id=None,
        execution_target="local",
    )
    task.bind_memory_snapshot(UUID(int=2), _SNAPSHOT_HASH)
    return task


def _scope(task: Task) -> ScopeContract:
    return ScopeContract.create(
        workspace_type=WorkspaceType.CHAT_SCRATCH,
        project_id=None,
        conversation_id=task.conversation_id,
        task_id=task.id,
        operation_mode=task.operation_mode,
        base_version_id=None,
        target_version_id=None,
        project_root=Path("scratch") / str(task.id),
        allowed_write_paths=(),
        forbidden_write_paths=(),
        execution_target="local",
        network_policy="open_web_safe",
        memory_read_scope=("current_conversation", "user_profile", "task_episode"),
        memory_write_scope=("current_conversation_draft",),
        memory_snapshot_id=task.memory_snapshot_id,
        memory_snapshot_hash=task.memory_snapshot_hash,
    )


def _turn() -> AssistantTurn:
    task = _task()
    return AssistantTurn.create(
        task=task,
        scope=_scope(task),
        profile_id="local-default",
        idempotency_key="turn-1",
    )


def test_message_is_immutable_and_requires_positive_sequence_and_content() -> None:
    message = Message.create(
        conversation_id=UUID(int=1),
        task_id=UUID(int=2),
        turn_id=UUID(int=3),
        sequence=1,
        role=MessageRole.USER,
        visibility=MessageVisibility.USER,
        content="Hello Fairy",
    )

    assert message.content == "Hello Fairy"
    with pytest.raises(AttributeError):
        message.content = "mutated"  # type: ignore[misc]
    with pytest.raises(ValueError, match="sequence"):
        Message.create(
            conversation_id=UUID(int=1),
            task_id=UUID(int=2),
            turn_id=None,
            sequence=0,
            role=MessageRole.SYSTEM_NOTICE,
            visibility=MessageVisibility.USER,
            content="notice",
        )
    with pytest.raises(ValueError, match="content"):
        Message.create(
            conversation_id=UUID(int=1),
            task_id=UUID(int=2),
            turn_id=None,
            sequence=1,
            role=MessageRole.SYSTEM_NOTICE,
            visibility=MessageVisibility.USER,
            content="   ",
        )


def test_turn_binds_core_scope_and_snapshot_and_rejects_scope_mismatch() -> None:
    task = _task()
    scope = _scope(task)

    turn = AssistantTurn.create(
        task=task,
        scope=scope,
        profile_id="local-default",
        idempotency_key="turn-1",
    )

    assert turn.task_id == task.id
    assert turn.conversation_id == task.conversation_id
    assert turn.scope_digest == scope.scope_digest
    assert turn.memory_snapshot_id == task.memory_snapshot_id
    assert turn.memory_snapshot_hash == task.memory_snapshot_hash

    other = _task()
    with pytest.raises(ValueError, match="Scope"):
        AssistantTurn.create(
            task=task,
            scope=_scope(other),
            profile_id="local-default",
            idempotency_key="turn-2",
        )


def test_turn_requires_a_task_bound_memory_snapshot() -> None:
    task = Task.create(
        project_id=None,
        conversation_id=UUID(int=1),
        user_request="Hello",
        operation_mode=OperationMode.ANSWER,
        base_version_id=None,
        execution_target="local",
    )
    scope = ScopeContract.create(
        workspace_type=WorkspaceType.CHAT_SCRATCH,
        project_id=None,
        conversation_id=task.conversation_id,
        task_id=task.id,
        operation_mode=task.operation_mode,
        base_version_id=None,
        target_version_id=None,
        project_root=Path("scratch") / str(task.id),
        allowed_write_paths=(),
        forbidden_write_paths=(),
        execution_target="local",
        network_policy="open_web_safe",
        memory_read_scope=("current_conversation",),
        memory_write_scope=("current_conversation_draft",),
    )

    with pytest.raises(ValueError, match="Memory Snapshot"):
        AssistantTurn.create(
            task=task,
            scope=scope,
            profile_id="local-default",
            idempotency_key="turn-1",
        )


def test_turn_state_machine_and_terminal_cancellation() -> None:
    turn = _turn()

    turn.start()
    assert turn.status is AssistantTurnStatus.RUNNING
    turn.wait_for_tool()
    assert turn.status is AssistantTurnStatus.WAITING_FOR_TOOL
    turn.resume()
    turn.complete(usage={"input_tokens": 12, "output_tokens": 7})

    assert turn.status is AssistantTurnStatus.COMPLETED
    assert turn.usage == {"input_tokens": 12, "output_tokens": 7}
    assert turn.completed_at is not None
    with pytest.raises(InvalidTransitionError, match="completed"):
        turn.cancel()


def test_turn_cancel_increments_revision_and_interruption_is_explicit_failure() -> None:
    cancelled = _turn()
    cancelled.start()
    cancelled.cancel()

    assert cancelled.status is AssistantTurnStatus.CANCELLED
    assert cancelled.cancellation_revision == 1
    assert cancelled.completed_at is not None

    interrupted = _turn()
    interrupted.start()
    interrupted.interrupt()

    assert interrupted.status is AssistantTurnStatus.FAILED
    assert interrupted.error_code == "WORKER_INTERRUPTED"


def test_tool_invocation_hash_is_canonical_and_transitions_are_guarded() -> None:
    turn = _turn()
    first = ToolInvocation.create(
        turn=turn,
        model_round=1,
        sequence=1,
        provider_call_id="call-1",
        tool_name="info.weather",
        scope_digest=turn.scope_digest,
        arguments={"units": "metric", "place": "Sydney"},
    )
    second = ToolInvocation.create(
        turn=turn,
        model_round=1,
        sequence=2,
        provider_call_id="call-2",
        tool_name="info.weather",
        scope_digest=turn.scope_digest,
        arguments={"place": "Sydney", "units": "metric"},
    )

    assert first.argument_hash == second.argument_hash
    first.queue(command_run_id=UUID(int=9))
    first.start()
    first.complete(
        public_summary="Sydney: 21 C",
        model_content="Sydney: 21 C",
        artifact_ids=(UUID(int=10),),
    )
    assert first.status is ToolInvocationStatus.COMPLETED
    assert first.public_summary == "Sydney: 21 C"
    assert first.artifact_ids == (UUID(int=10),)

    with pytest.raises(InvalidTransitionError, match="completed"):
        first.fail(error_code="UPSTREAM_ERROR")


def test_tool_invocation_rejects_model_scope_and_non_json_arguments() -> None:
    turn = _turn()
    with pytest.raises(ValueError, match="Scope"):
        ToolInvocation.create(
            turn=turn,
            model_round=1,
            sequence=1,
            provider_call_id="call-scope",
            tool_name="info.weather",
            scope_digest="b" * 64,
            arguments={"place": "Sydney"},
        )
    with pytest.raises(ValueError, match="JSON"):
        ToolInvocation.create(
            turn=turn,
            model_round=1,
            sequence=1,
            provider_call_id="call-json",
            tool_name="info.weather",
            scope_digest=turn.scope_digest,
            arguments={"bad": {1, 2}},
        )
