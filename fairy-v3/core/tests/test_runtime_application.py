from __future__ import annotations

from pathlib import Path

import pytest

from fairy_core.application.runtime import (
    PreviewResolveRequest,
    PreviewStartRequest,
    PreviewStopRequest,
)
from fairy_core.commanding import EventVisibility
from fairy_core.commanding.policy import PermissionProfile
from fairy_core.domain.errors import IdempotencyConflictError, InvalidTransitionError
from fairy_core.domain.execution import (
    ArtifactType,
    PreviewStatus,
    PreviewVisibility,
    RuntimeStatus,
)
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import TaskStatus, WorkspaceType
from fairy_core.runtime.models import RuntimeExecutorError
from tests.runtime_support import build_runtime_stack, build_scratch_runtime_stack


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
        artifacts = unit_of_work.state.artifacts_for_task(stack.task.task.id)
        events = unit_of_work.commands.events_after(
            cursor=0,
            allowed_visibilities={EventVisibility.USER},
        )
    assert conversation is not None
    assert conversation.active_preview_id == context.preview.id
    manifests = [
        artifact
        for artifact in artifacts
        if artifact.artifact_type is ArtifactType.PREVIEW_MANIFEST
    ]
    assert len(manifests) == 1
    assert manifests[0].metadata["preview_id"] == str(context.preview.id)
    assert manifests[0].metadata["runtime_id"] == str(context.runtime.id)
    assert manifests[0].metadata["entry_path"] == "index.html"
    assert manifests[0].metadata["url"] == context.preview.url
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


def test_scratch_chat_uses_versioned_workspace_and_auto_promotes_preview(
    tmp_path: Path,
) -> None:
    stack = build_scratch_runtime_stack(tmp_path)
    task = stack.task.task
    assert task.project_id is None
    assert task.workspace_id is not None
    assert task.target_version_id is not None

    context = stack.runtime.start_preview(
        PreviewStartRequest(
            task_id=task.id,
            workspace_id=task.workspace_id,
            version_id=task.target_version_id,
            idempotency_key="scratch-runtime:preview",
        )
    )

    assert context.preview.status is PreviewStatus.READY
    assert stack.executor.start_calls[0].project_id is None
    assert stack.executor.start_calls[0].workspace_id == task.workspace_id
    with stack.factory() as unit_of_work:
        workspace = unit_of_work.state.get_workspace(task.workspace_id)
        version = unit_of_work.state.get_version(task.target_version_id)
        conversation = unit_of_work.state.get_conversation(task.conversation_id)
        index = unit_of_work.project_indexes.get(task.target_version_id)
        events = unit_of_work.commands.events_after(
            cursor=0,
            allowed_visibilities={EventVisibility.USER},
        )
    assert workspace is not None and workspace.active_version_id == task.target_version_id
    assert workspace.active_preview_id == context.preview.id
    assert version is not None and version.visibility.value == "project_active"
    assert conversation is not None and conversation.base_version_id == task.target_version_id
    assert index is not None and any(item.path == "index.html" for item in index.files)
    assert "workspace.version.auto_promoted" in {event.event_type for event in events}


def test_preview_start_uses_persisted_execution_policy(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    with stack.factory() as unit_of_work:
        current = unit_of_work.execution_settings.get()
        unit_of_work.execution_settings.update(
            profile=PermissionProfile.OBSERVE,
            capability_overrides={},
            expected_revision=current.revision,
            idempotency_key="runtime:policy:observe",
        )
        unit_of_work.commit()

    with pytest.raises(RuntimeExecutorError) as captured:
        stack.runtime.start_preview(
            PreviewStartRequest(
                task_id=stack.task.task.id,
                idempotency_key="preview:observe:rejected",
            )
        )

    assert captured.value.error_code == "CAPABILITY_NOT_AVAILABLE"
    assert stack.executor.start_calls == []


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
    checkpoint = stack.core.review_task(stack.task.task.id)

    with stack.factory() as unit_of_work:
        manifests = [
            artifact
            for artifact in unit_of_work.state.artifacts_for_task(stack.task.task.id)
            if artifact.artifact_type is ArtifactType.PREVIEW_MANIFEST
        ]
    assert len(manifests) == 1
    assert checkpoint.preview_artifact_id == manifests[0].id

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


def test_interrupted_project_active_preview_can_restart_without_reopening_task(
    tmp_path: Path,
) -> None:
    stack = build_runtime_stack(tmp_path)
    request = PreviewStartRequest(
        task_id=stack.task.task.id,
        idempotency_key="preview:accepted-restart",
    )
    context = stack.runtime.start_preview(request)
    stack.core.review_task(stack.task.task.id)
    stack.core.accept_task_version(
        task_id=stack.task.task.id,
        expected_project_revision=stack.project_revision,
        user_confirmed=True,
    )
    stack.executor.probes.clear()
    stack.runtime.recover_interrupted(verify_running=True)

    restarted = stack.runtime.start_preview(request)

    assert restarted.preview.id == context.preview.id
    assert restarted.preview.status is PreviewStatus.READY
    assert restarted.preview.visibility is PreviewVisibility.PROJECT_ACTIVE
    assert restarted.task.status is TaskStatus.ACCEPTED
    assert len(stack.executor.start_calls) == 2


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
    unrelated = stack.runtime.resolve_preview(PreviewResolveRequest(conversation_id=other.id))

    assert current is not None and current.preview.id == context.preview.id
    assert unrelated is None
    with pytest.raises(ValueError, match="Conversation"):
        stack.runtime.resolve_preview(
            PreviewResolveRequest(
                conversation_id=other.id,
                preview_id=context.preview.id,
            )
        )


def test_preview_operations_require_exact_workspace_version_binding(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    task = stack.task.task
    assert task.target_version_id is not None
    with stack.factory() as unit_of_work:
        workspace = unit_of_work.state.get_workspace(task.workspace_id)
        assert workspace is not None
    binding = {
        "task_id": task.id,
        "workspace_id": task.workspace_id,
        "version_id": task.target_version_id,
    }

    with pytest.raises(RuntimeExecutorError) as mismatched_start:
        stack.runtime.start_preview(
            PreviewStartRequest(
                **{**binding, "workspace_id": new_id()},
                expected_workspace_revision=workspace.revision,
                idempotency_key="preview:wrong-workspace",
            )
        )
    assert mismatched_start.value.error_code == "SCOPE_MISMATCH"

    context = stack.runtime.start_preview(
        PreviewStartRequest(
            **binding,
            expected_workspace_revision=workspace.revision,
            idempotency_key="preview:strict-binding",
        )
    )
    with pytest.raises(RuntimeExecutorError) as mismatched_resolve:
        stack.runtime.resolve_preview(PreviewResolveRequest(**{**binding, "version_id": new_id()}))
    assert mismatched_resolve.value.error_code == "SCOPE_MISMATCH"
    with pytest.raises(RuntimeExecutorError) as mismatched_stop:
        stack.runtime.stop_preview(
            PreviewStopRequest(
                preview_id=context.preview.id,
                idempotency_key="preview:wrong-stop",
                **{**binding, "task_id": new_id()},
                expected_workspace_revision=workspace.revision,
            )
        )
    assert mismatched_stop.value.error_code == "SCOPE_MISMATCH"
