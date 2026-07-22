from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fairy_core.application.core import TaskContext
from fairy_core.application.runtime_contracts import (
    PreviewActivateRequest,
    PreviewActivationOutcome,
    PreviewStartRequest,
)
from fairy_core.contracts.models import ChangesetProposal, ExecutionTarget, FileMutation, TaskCreate
from fairy_core.domain.execution import PreviewStatus
from fairy_core.domain.models import OperationMode, TaskStatus, WorkspaceType
from tests.runtime_support import RuntimeStack, build_runtime_stack


def _create_runnable_task(stack: RuntimeStack, ordinal: int) -> TaskContext:
    conversation = stack.core.create_conversation(
        project_id=None,
        workspace_type=WorkspaceType.CHAT_SCRATCH,
    )
    task = stack.core.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request=f"Build preview {ordinal}",
            operation_mode=OperationMode.ANSWER,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key=f"pool:task:{ordinal}",
        )
    )
    pending = stack.core.propose_changeset(
        ChangesetProposal(
            task_id=task.task.id,
            files=(
                FileMutation(
                    path="index.html",
                    content=f"<h1>Preview {ordinal}</h1>",
                ),
            ),
            reason="Create a static entry",
            idempotency_key=f"pool:changeset:{ordinal}",
        )
    )
    stack.core.decide_approval(
        approval_id=pending.approval.id,
        approved=True,
        decided_by="user",
    )
    return task


def _start_and_complete(stack: RuntimeStack, task: TaskContext, ordinal: int):
    context = stack.runtime.start_preview(
        PreviewStartRequest(
            task_id=task.task.id,
            idempotency_key=f"pool:preview:{ordinal}",
        )
    )
    with stack.factory() as unit_of_work:
        persisted = unit_of_work.state.get_task(task.task.id)
        assert persisted is not None
        persisted.transition_to(TaskStatus.REVIEWING)
        persisted.transition_to(TaskStatus.READY)
        unit_of_work.state.save_task(persisted)
        unit_of_work.commit()
    return context


def _activate(stack: RuntimeStack, task: TaskContext, ordinal: int):
    return stack.runtime.activate_preview(
        PreviewActivateRequest(
            task_id=task.task.id,
            workspace_id=task.task.workspace_id,
            version_id=task.task.target_version_id,
            idempotency_key=f"pool:activate:{ordinal}",
        )
    )


def test_fourth_activation_evicts_least_recently_accessed_completed_preview(
    tmp_path: Path,
) -> None:
    stack = build_runtime_stack(tmp_path)
    tasks = [_create_runnable_task(stack, ordinal) for ordinal in range(4)]
    contexts = [_start_and_complete(stack, task, ordinal) for ordinal, task in enumerate(tasks[:3])]
    accessed_at = datetime.now(UTC)
    with stack.factory() as unit_of_work:
        for ordinal, context in enumerate(contexts):
            unit_of_work.state.touch_preview_access(
                context.preview.id,
                accessed_at=accessed_at + timedelta(seconds=ordinal),
            )
        unit_of_work.commit()

    result = _activate(stack, tasks[3], 3)

    assert result.outcome is PreviewActivationOutcome.READY
    assert result.context is not None
    assert result.adapter == "static"
    assert result.capacity == 3
    assert result.active_count == 3
    assert result.evicted_preview_id == contexts[0].preview.id
    with stack.factory() as unit_of_work:
        evicted = unit_of_work.state.get_preview(contexts[0].preview.id)
        active = unit_of_work.state.active_previews()
    assert evicted is not None and evicted.status is PreviewStatus.STOPPED
    assert {item.id for item in active} == {
        contexts[1].preview.id,
        contexts[2].preview.id,
        result.context.preview.id,
    }

    restarted = _activate(stack, tasks[0], 4)

    assert restarted.outcome is PreviewActivationOutcome.READY
    assert restarted.context is not None
    assert restarted.context.preview.id != contexts[0].preview.id
    assert restarted.evicted_preview_id == contexts[1].preview.id
    assert len(stack.executor.start_calls) == 5


def test_activation_waits_when_every_runtime_is_protected(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    tasks = [_create_runnable_task(stack, ordinal) for ordinal in range(4)]
    for ordinal, task in enumerate(tasks[:3]):
        stack.runtime.start_preview(
            PreviewStartRequest(
                task_id=task.task.id,
                idempotency_key=f"pool:protected:{ordinal}",
            )
        )

    result = _activate(stack, tasks[3], 3)

    assert result.outcome is PreviewActivationOutcome.WAITING_FOR_SLOT
    assert result.context is None
    assert result.active_count == 3
    assert result.capacity == 3
    assert result.evicted_preview_id is None
    assert len(stack.executor.start_calls) == 3


def test_idle_reaper_stops_completed_previews_after_ten_minutes(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    task = _create_runnable_task(stack, 1)
    context = _start_and_complete(stack, task, 1)

    stopped = stack.runtime.reap_idle_previews(
        now=context.preview.last_accessed_at + timedelta(minutes=10, seconds=1)
    )

    assert stopped == (context.preview.id,)
    with stack.factory() as unit_of_work:
        preview = unit_of_work.state.get_preview(context.preview.id)
    assert preview is not None and preview.status is PreviewStatus.STOPPED
