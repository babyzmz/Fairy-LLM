from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.execution import (
    Artifact,
    ArtifactType,
    ArtifactVisibility,
    PreviewHealth,
    PreviewSession,
    PreviewStatus,
    PreviewVisibility,
    RuntimeHealth,
    RuntimeKind,
    RuntimeSession,
    RuntimeStatus,
)
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import OperationMode, ScopeContract, WorkspaceType

NOW = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
PROPERTY_ROOT = Path("C:/fairy-runtime-properties")

RUNTIME_EDGES = {
    (RuntimeStatus.CREATED, RuntimeStatus.STARTING),
    (RuntimeStatus.CREATED, RuntimeStatus.INTERRUPTED),
    (RuntimeStatus.STARTING, RuntimeStatus.RUNNING),
    (RuntimeStatus.STARTING, RuntimeStatus.FAILED),
    (RuntimeStatus.STARTING, RuntimeStatus.INTERRUPTED),
    (RuntimeStatus.RUNNING, RuntimeStatus.STOPPING),
    (RuntimeStatus.RUNNING, RuntimeStatus.FAILED),
    (RuntimeStatus.RUNNING, RuntimeStatus.INTERRUPTED),
    (RuntimeStatus.STOPPING, RuntimeStatus.STOPPED),
    (RuntimeStatus.STOPPING, RuntimeStatus.FAILED),
    (RuntimeStatus.STOPPING, RuntimeStatus.INTERRUPTED),
    (RuntimeStatus.FAILED, RuntimeStatus.STARTING),
    (RuntimeStatus.INTERRUPTED, RuntimeStatus.STARTING),
    (RuntimeStatus.INTERRUPTED, RuntimeStatus.STOPPING),
    (RuntimeStatus.INTERRUPTED, RuntimeStatus.FAILED),
}

PREVIEW_EDGES = {
    (PreviewStatus.CREATED, PreviewStatus.STARTING),
    (PreviewStatus.CREATED, PreviewStatus.INTERRUPTED),
    (PreviewStatus.STARTING, PreviewStatus.READY),
    (PreviewStatus.STARTING, PreviewStatus.FAILED),
    (PreviewStatus.STARTING, PreviewStatus.INTERRUPTED),
    (PreviewStatus.READY, PreviewStatus.STOPPING),
    (PreviewStatus.READY, PreviewStatus.FAILED),
    (PreviewStatus.READY, PreviewStatus.INTERRUPTED),
    (PreviewStatus.STOPPING, PreviewStatus.STOPPED),
    (PreviewStatus.STOPPING, PreviewStatus.FAILED),
    (PreviewStatus.STOPPING, PreviewStatus.INTERRUPTED),
    (PreviewStatus.FAILED, PreviewStatus.STARTING),
    (PreviewStatus.INTERRUPTED, PreviewStatus.STARTING),
    (PreviewStatus.INTERRUPTED, PreviewStatus.STOPPING),
    (PreviewStatus.INTERRUPTED, PreviewStatus.FAILED),
}


def _scope(tmp_path: Path) -> ScopeContract:
    project_id = new_id()
    version_id = new_id()
    return ScopeContract.create(
        workspace_type=WorkspaceType.PROJECT_CHAT,
        project_id=project_id,
        conversation_id=new_id(),
        task_id=new_id(),
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=version_id,
        target_version_id=version_id,
        project_root=tmp_path,
        allowed_write_paths=(tmp_path,),
        forbidden_write_paths=(),
        execution_target="local",
        network_policy="off",
        memory_read_scope=("project_canonical",),
        memory_write_scope=("conversation_draft",),
    )


def _runtime(status: RuntimeStatus, tmp_path: Path) -> RuntimeSession:
    scope = _scope(tmp_path)
    handle = (
        "static:runtime-property"
        if status
        in {
            RuntimeStatus.RUNNING,
            RuntimeStatus.STOPPING,
            RuntimeStatus.INTERRUPTED,
        }
        else None
    )
    port = 43125 if handle is not None else None
    health = {
        RuntimeStatus.CREATED: RuntimeHealth.UNKNOWN,
        RuntimeStatus.STARTING: RuntimeHealth.STARTING,
        RuntimeStatus.RUNNING: RuntimeHealth.HEALTHY,
        RuntimeStatus.STOPPING: RuntimeHealth.STOPPING,
        RuntimeStatus.STOPPED: RuntimeHealth.STOPPED,
        RuntimeStatus.FAILED: RuntimeHealth.UNHEALTHY,
        RuntimeStatus.INTERRUPTED: RuntimeHealth.INTERRUPTED,
    }[status]
    return RuntimeSession.restore(
        id=new_id(),
        project_id=scope.project_id,
        conversation_id=scope.conversation_id,
        task_id=scope.task_id,
        version_id=scope.target_version_id,
        project_root=scope.project_root,
        execution_target="local",
        kind=RuntimeKind.STATIC_SITE,
        executor="rust_local_worker",
        executor_handle=handle,
        port=port,
        status=status,
        health=health,
        error_code=(
            "WORKER_INTERRUPTED"
            if status is RuntimeStatus.INTERRUPTED
            else "RUNTIME_FAILED"
            if status is RuntimeStatus.FAILED
            else None
        ),
        idempotency_key="runtime:property",
        revision=4,
        created_at=NOW,
        updated_at=NOW,
    )


def _preview(status: PreviewStatus, tmp_path: Path) -> PreviewSession:
    scope = _scope(tmp_path)
    url = (
        "http://127.0.0.1:43125/preview-property/"
        if status in {PreviewStatus.READY, PreviewStatus.STOPPING, PreviewStatus.INTERRUPTED}
        else None
    )
    health = {
        PreviewStatus.CREATED: PreviewHealth.UNKNOWN,
        PreviewStatus.STARTING: PreviewHealth.STARTING,
        PreviewStatus.READY: PreviewHealth.HEALTHY,
        PreviewStatus.STOPPING: PreviewHealth.STOPPING,
        PreviewStatus.STOPPED: PreviewHealth.STOPPED,
        PreviewStatus.FAILED: PreviewHealth.UNHEALTHY,
        PreviewStatus.INTERRUPTED: PreviewHealth.INTERRUPTED,
    }[status]
    return PreviewSession.restore(
        id=new_id(),
        project_id=scope.project_id,
        conversation_id=scope.conversation_id,
        task_id=scope.task_id,
        version_id=scope.target_version_id,
        runtime_id=new_id(),
        project_root=scope.project_root,
        execution_target="local",
        url=url,
        visibility=PreviewVisibility.CHAT_DRAFT,
        status=status,
        health=health,
        error_code=(
            "WORKER_INTERRUPTED"
            if status is PreviewStatus.INTERRUPTED
            else "PREVIEW_FAILED"
            if status is PreviewStatus.FAILED
            else None
        ),
        idempotency_key="preview:property",
        revision=4,
        created_at=NOW,
        updated_at=NOW,
    )


def _move_runtime(runtime: RuntimeSession, target: RuntimeStatus) -> None:
    actions = {
        RuntimeStatus.STARTING: runtime.begin_start,
        RuntimeStatus.RUNNING: lambda: runtime.mark_running(
            executor_handle="static:runtime-property",
            port=43125,
        ),
        RuntimeStatus.STOPPING: runtime.begin_stop,
        RuntimeStatus.STOPPED: runtime.mark_stopped,
        RuntimeStatus.FAILED: lambda: runtime.mark_failed("RUNTIME_FAILED"),
        RuntimeStatus.INTERRUPTED: runtime.mark_interrupted,
    }
    action = actions.get(target)
    if action is None:
        raise InvalidTransitionError("cannot transition Runtime to created")
    action()


def _move_preview(preview: PreviewSession, target: PreviewStatus) -> None:
    actions = {
        PreviewStatus.STARTING: preview.begin_start,
        PreviewStatus.READY: lambda: preview.mark_ready("http://127.0.0.1:43125/preview-property/"),
        PreviewStatus.STOPPING: preview.begin_stop,
        PreviewStatus.STOPPED: preview.mark_stopped,
        PreviewStatus.FAILED: lambda: preview.mark_failed("PREVIEW_FAILED"),
        PreviewStatus.INTERRUPTED: preview.mark_interrupted,
    }
    action = actions.get(target)
    if action is None:
        raise InvalidTransitionError("cannot transition Preview to created")
    action()


@given(
    source=st.sampled_from(tuple(RuntimeStatus)),
    target=st.sampled_from(tuple(RuntimeStatus)),
)
def test_runtime_transition_matrix(
    source: RuntimeStatus,
    target: RuntimeStatus,
) -> None:
    runtime = _runtime(source, PROPERTY_ROOT)
    if (source, target) in RUNTIME_EDGES:
        _move_runtime(runtime, target)
        assert runtime.status is target
        assert runtime.revision == 5
    else:
        with pytest.raises(InvalidTransitionError):
            _move_runtime(runtime, target)


@given(
    source=st.sampled_from(tuple(PreviewStatus)),
    target=st.sampled_from(tuple(PreviewStatus)),
)
def test_preview_transition_matrix(
    source: PreviewStatus,
    target: PreviewStatus,
) -> None:
    preview = _preview(source, PROPERTY_ROOT)
    if (source, target) in PREVIEW_EDGES:
        _move_preview(preview, target)
        assert preview.status is target
        assert preview.revision == 5
    else:
        with pytest.raises(InvalidTransitionError):
            _move_preview(preview, target)


@pytest.mark.parametrize(
    "url",
    (
        "https://127.0.0.1:43125/preview/",
        "http://localhost:43125/preview/",
        "http://0.0.0.0:43125/preview/",
        "http://127.0.0.1/preview/",
        "http://user@127.0.0.1:43125/preview/",
        "http://127.0.0.1:43125/preview/#fragment",
    ),
)
def test_preview_rejects_non_executor_local_urls(url: str, tmp_path: Path) -> None:
    preview = _preview(PreviewStatus.STARTING, tmp_path)
    with pytest.raises(ValueError):
        preview.mark_ready(url)


def test_artifact_validates_manifest_and_freezes_metadata() -> None:
    metadata = {"title": "Preview"}
    artifact = Artifact.create(
        project_id=new_id(),
        conversation_id=new_id(),
        task_id=new_id(),
        version_id=new_id(),
        artifact_type=ArtifactType.PREVIEW_MANIFEST,
        visibility=ArtifactVisibility.CONVERSATION,
        storage_location="previews/manifest.json",
        media_type="application/json",
        byte_length=42,
        content_hash="b" * 64,
        metadata=metadata,
    )
    metadata["title"] = "mutated"

    assert artifact.metadata["title"] == "Preview"
    with pytest.raises(TypeError):
        artifact.metadata["title"] = "blocked"  # type: ignore[index]
    with pytest.raises(ValueError):
        Artifact.create(
            project_id=None,
            conversation_id=new_id(),
            task_id=new_id(),
            version_id=None,
            artifact_type=ArtifactType.LOG,
            visibility=ArtifactVisibility.PRIVATE,
            storage_location="logs/task.txt",
            media_type="text/plain",
            byte_length=-1,
            content_hash="not-a-hash",
            metadata={},
        )
