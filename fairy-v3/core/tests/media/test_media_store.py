from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.domain.models import (
    Conversation,
    OperationMode,
    Project,
    ProjectResidency,
    ScopeContract,
    Task,
    Version,
    VersionVisibility,
    WorkspaceType,
)
from fairy_core.media.models import (
    MediaGenerationJob,
    MediaGenerationKind,
    MediaGenerationStatus,
)
from fairy_core.model_catalog.models import ModelEndpointKind
from fairy_core.storage import SqliteStateStore


def _scope(store: SqliteStateStore, tmp_path: Path) -> ScopeContract:
    project = Project.create(name="Media", residency=ProjectResidency.LOCAL_ONLY)
    conversation = Conversation.create(
        project_id=project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=None,
    )
    task = Task.create(
        project_id=project.id,
        conversation_id=conversation.id,
        user_request="Generate an image",
        operation_mode=OperationMode.CREATE_NEW_VERSION,
        base_version_id=None,
        execution_target="local",
    )
    version = Version.create(
        project_id=project.id,
        source_conversation_id=conversation.id,
        source_task_id=task.id,
        parent_version_id=None,
        project_root=tmp_path / "versions" / str(task.id),
        visibility=VersionVisibility.CHAT_DRAFT,
    )
    task.bind_target_version(version.id)
    store.save_project(project)
    store.save_conversation(conversation)
    store.save_task(task, idempotency_key=f"task:{task.id}")
    store.save_version(version)
    return ScopeContract.create(
        workspace_type=WorkspaceType.PROJECT_CHAT,
        project_id=project.id,
        conversation_id=conversation.id,
        task_id=task.id,
        operation_mode=OperationMode.CREATE_NEW_VERSION,
        base_version_id=version.id,
        target_version_id=version.id,
        project_root=version.project_root,
        allowed_write_paths=(version.project_root,),
        forbidden_write_paths=(),
        execution_target="local",
        network_policy="off",
        memory_read_scope=("project_canonical",),
        memory_write_scope=("conversation_draft",),
    )


def _job(scope: ScopeContract, *, fingerprint: str = "b" * 64) -> MediaGenerationJob:
    assert scope.workspace_id is not None
    assert scope.target_version_id is not None
    return MediaGenerationJob.create(
        project_id=scope.project_id,
        workspace_id=scope.workspace_id,
        conversation_id=scope.conversation_id,
        task_id=scope.task_id,
        version_id=scope.target_version_id,
        turn_id=None,
        command_run_id=uuid4(),
        scope_digest=scope.scope_digest,
        kind=MediaGenerationKind.IMAGE,
        model_id="google/gemini-3.1-flash-lite-image",
        endpoint_kind=ModelEndpointKind.IMAGES,
        output_path="generated/image.png",
        request_spec={"prompt": "A precise image"},
        request_fingerprint=fingerprint,
        idempotency_key="media:image:round-trip",
    )


def test_media_job_round_trip_recovery_and_revision_fence(tmp_path: Path) -> None:
    database = tmp_path / "core.db"
    store = SqliteStateStore(database)
    job = _job(_scope(store, tmp_path))
    assert store.save_media_job(job) == job

    stale = replace(job)
    job.begin()
    store.update_media_job(
        job,
        expected_revision=0,
        expected_status=MediaGenerationStatus.CREATED,
    )
    stale.begin()
    with pytest.raises(VersionConflictError):
        store.update_media_job(
            stale,
            expected_revision=0,
            expected_status=MediaGenerationStatus.CREATED,
        )
    store.close()

    restarted = SqliteStateStore(database)
    assert restarted.get_media_job(job.id) == job
    assert restarted.find_media_job_by_idempotency_key(job.idempotency_key) == job
    assert restarted.recoverable_media_jobs() == [job]


def test_media_job_idempotency_fingerprint_is_immutable(tmp_path: Path) -> None:
    store = SqliteStateStore(tmp_path / "core.db")
    scope = _scope(store, tmp_path)
    job = _job(scope)
    store.save_media_job(job)

    replay = _job(scope)
    assert store.save_media_job(replay) == job
    with pytest.raises(IdempotencyConflictError):
        store.save_media_job(_job(scope, fingerprint="c" * 64))
