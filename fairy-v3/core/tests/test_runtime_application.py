from __future__ import annotations

from pathlib import Path

import pytest

from fairy_core.application.runtime import (
    PreviewResolveRequest,
    PreviewStartRequest,
    PreviewStopRequest,
)
from fairy_core.commanding import EventVisibility
from fairy_core.domain.errors import IdempotencyConflictError, InvalidTransitionError
from fairy_core.domain.execution import PreviewStatus, PreviewVisibility, RuntimeStatus
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import TaskStatus, WorkspaceType
from fairy_core.runtime.models import RuntimeExecutorError
from tests.runtime_support import build_runtime_stack


def test_static_preview_start_replay_stop_and_visible_events(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    request = PreviewStartRequest(
        task_id=stack.task.task.id,
        idempotency_key="preview:start",
    )

    context = stack.runtime.start_preview(request)
    replay = stack.runtime.start_preview(request)

    assert replay.preview.id == context.preview.id
    assert len(stack.executor.start_calls) == 1
    assert context.runtime.status is RuntimeStatus.RUNNING
    assert context.preview.status is PreviewStatus.READY
    assert context.task.status is TaskStatus.PREVIEWING
    health = stack.runtime.runtime_health(stack.task.task.id)
    assert health.executor.available is True
    assert health.runtime.id == context.runtime.id
    with stack.factory() as unit_of_work:
        conversation = unit_of_work.state.get_conversation(stack.task.task.conversation_id)
        events = unit_of_work.commands.events_after(
            cursor=0,
            allowed_visibilities={EventVisibility.USER},
        )
    assert conversation is not None
    assert conversation.active_preview_id == context.preview.id
    assert {event.event_type for event in events} >= {
        "preview.starting",
        "preview.ready",
    }

    stopped = stack.runtime.stop_preview(
        PreviewStopRequest(
            preview_id=context.preview.id,
            idempotency_key="preview:stop",
        )
    )
    stop_replay = stack.runtime.stop_preview(
        PreviewStopRequest(
            preview_id=context.preview.id,
            idempotency_key="preview:stop",
        )
    )

    assert stopped.status is PreviewStatus.STOPPED
    assert stop_replay.status is PreviewStatus.STOPPED
    assert len(stack.executor.stop_calls) == 1


def test_preview_start_failure_persists_typed_failure_without_ready_state(
    tmp_path: Path,
) -> None:
    stack = build_runtime_stack(tmp_path)
    stack.executor.start_failure = RuntimeExecutorError(
        "worker unavailable",
        error_code="WORKER_INTERRUPTED",
    )

    with pytest.raises(RuntimeExecutorError):
        stack.runtime.start_preview(
            PreviewStartRequest(
                task_id=stack.task.task.id,
                idempotency_key="preview:failure",
            )
        )

    with stack.factory() as unit_of_work:
        preview = unit_of_work.state.preview_for_task(
            stack.task.task.id,
            include_terminal=True,
        )
        runtimes = unit_of_work.state.runtimes_for_task(stack.task.task.id)
        task = unit_of_work.state.get_task(stack.task.task.id)
    assert preview is not None
    assert preview.status is PreviewStatus.FAILED
    assert preview.url is None
    assert runtimes[-1].status is RuntimeStatus.FAILED
    assert task is not None and task.status is TaskStatus.FAILED


def test_preview_idempotency_key_cannot_rebind_to_another_task(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    stack.runtime.start_preview(
        PreviewStartRequest(
            task_id=stack.task.task.id,
            idempotency_key="preview:bound",
        )
    )

    with pytest.raises(IdempotencyConflictError):
        stack.runtime.start_preview(
            PreviewStartRequest(
                task_id=new_id(),
                idempotency_key="preview:bound",
            )
        )


def test_ready_preview_is_promoted_on_accept_without_restart(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    context = stack.runtime.start_preview(
        PreviewStartRequest(
            task_id=stack.task.task.id,
            idempotency_key="preview:accept",
        )
    )
    stack.core.review_task(stack.task.task.id)

    project = stack.core.accept_task_version(
        task_id=stack.task.task.id,
        expected_project_revision=stack.project_revision,
        user_confirmed=True,
    )

    with stack.factory() as unit_of_work:
        promoted = unit_of_work.state.get_preview(context.preview.id)
        conversation = unit_of_work.state.get_conversation(stack.task.task.conversation_id)
    assert project.active_preview_id == context.preview.id
    assert promoted is not None
    assert promoted.visibility is PreviewVisibility.PROJECT_ACTIVE
    assert promoted.status is PreviewStatus.READY
    assert conversation is not None and conversation.active_preview_id == context.preview.id
    assert stack.executor.stop_calls == []


def test_discard_is_blocked_until_preview_is_stopped(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    context = stack.runtime.start_preview(
        PreviewStartRequest(
            task_id=stack.task.task.id,
            idempotency_key="preview:discard",
        )
    )

    with pytest.raises(InvalidTransitionError, match="Preview"):
        stack.core.discard_task_version(stack.task.task.id)

    stack.runtime.stop_preview(
        PreviewStopRequest(
            preview_id=context.preview.id,
            idempotency_key="preview:discard:stop",
        )
    )
    discarded = stack.core.discard_task_version(stack.task.task.id)
    assert discarded.status is TaskStatus.REJECTED


def test_preview_resolver_never_uses_project_wide_newest_fallback(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    context = stack.runtime.start_preview(
        PreviewStartRequest(
            task_id=stack.task.task.id,
            idempotency_key="preview:resolve",
        )
    )
    project_id = stack.task.task.project_id
    assert project_id is not None
    other = stack.core.create_conversation(
        project_id=project_id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )

    current = stack.runtime.resolve_preview(
        PreviewResolveRequest(conversation_id=stack.task.task.conversation_id)
    )
    unrelated = stack.runtime.resolve_preview(
        PreviewResolveRequest(conversation_id=other.id)
    )

    assert current is not None and current.preview.id == context.preview.id
    assert unrelated is None
    with pytest.raises(ValueError, match="Conversation"):
        stack.runtime.resolve_preview(
            PreviewResolveRequest(
                conversation_id=other.id,
                preview_id=context.preview.id,
            )
        )
