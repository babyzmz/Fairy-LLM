from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from fairy_core.domain.errors import InvalidTransitionError, VersionConflictError
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import (
    Conversation,
    OperationMode,
    Project,
    ProjectResidency,
    ScopeContract,
    Task,
    TaskStatus,
    Version,
    VersionVisibility,
    WorkspaceType,
)


def test_new_id_is_uuid7_and_sorts_by_creation_order() -> None:
    first = new_id()
    second = new_id()

    assert first.version == 7
    assert second.version == 7
    assert first.int < second.int


def test_project_conversation_task_and_version_are_explicitly_linked(tmp_path: Path) -> None:
    project = Project.create(name="Example", residency=ProjectResidency.LOCAL_ONLY)
    base = Version.create(
        project_id=project.id,
        source_conversation_id=None,
        source_task_id=None,
        parent_version_id=None,
        project_root=tmp_path / "base",
        visibility=VersionVisibility.PROJECT_ACTIVE,
    )
    project.accept_version(base.id, expected_revision=0)
    conversation = Conversation.create(
        project_id=project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=base.id,
    )
    task = Task.create(
        project_id=project.id,
        conversation_id=conversation.id,
        user_request="Add a pricing section",
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=base.id,
        execution_target="local",
    )
    draft = Version.create(
        project_id=project.id,
        source_conversation_id=conversation.id,
        source_task_id=task.id,
        parent_version_id=base.id,
        project_root=tmp_path / "draft",
        visibility=VersionVisibility.CHAT_DRAFT,
    )
    task.bind_target_version(draft.id)

    assert conversation.project_id == project.id
    assert task.conversation_id == conversation.id
    assert task.base_version_id == project.active_version_id
    assert task.target_version_id == draft.id
    assert draft.parent_version_id == base.id


def test_task_rejects_invalid_status_transition() -> None:
    task = Task.create(
        project_id=new_id(),
        conversation_id=new_id(),
        user_request="Build it",
        operation_mode=OperationMode.CREATE_NEW_VERSION,
        base_version_id=new_id(),
        execution_target="cloud",
    )

    task.transition_to(TaskStatus.RESOLVING_SCOPE)
    with pytest.raises(InvalidTransitionError):
        task.transition_to(TaskStatus.READY)


def test_conversation_metadata_is_revision_fenced_and_deleted_state_is_terminal() -> None:
    conversation = Conversation.create(
        project_id=None,
        workspace_type=WorkspaceType.CHAT_SCRATCH,
        base_version_id=None,
    )
    conversation.update_metadata(
        title="Pinned notes",
        pinned=True,
        expected_revision=0,
    )

    assert conversation.title == "Pinned notes"
    assert conversation.pinned_at is not None
    assert conversation.revision == 1
    with pytest.raises(VersionConflictError):
        conversation.update_metadata(title="Stale", pinned=None, expected_revision=0)
    conversation.delete(expected_revision=1)
    assert conversation.deleted_at is not None
    assert conversation.pinned_at is None
    with pytest.raises(InvalidTransitionError, match="deleted"):
        conversation.update_metadata(title="Too late", pinned=None, expected_revision=2)


def test_task_metadata_is_revision_fenced_and_only_terminal_tasks_archive() -> None:
    task = Task.create(
        project_id=new_id(),
        conversation_id=new_id(),
        user_request="Build it",
        operation_mode=OperationMode.CREATE_NEW_VERSION,
        base_version_id=new_id(),
        execution_target="local",
    )
    task.update_metadata(display_title="Build release", pinned=True, expected_revision=0)

    assert task.display_title == "Build release"
    assert task.metadata_revision == 1
    with pytest.raises(VersionConflictError):
        task.update_metadata(display_title="Stale", pinned=None, expected_revision=0)
    with pytest.raises(InvalidTransitionError):
        task.archive(expected_revision=1)
    task.status = TaskStatus.READY
    task.archive(expected_revision=1)
    assert task.status is TaskStatus.ARCHIVED
    assert task.pinned_at is None
    assert task.metadata_revision == 2


def test_task_memory_snapshot_binding_is_idempotent_but_cannot_be_replaced() -> None:
    task = Task.create(
        project_id=new_id(),
        conversation_id=new_id(),
        user_request="Build it",
        operation_mode=OperationMode.CREATE_NEW_VERSION,
        base_version_id=new_id(),
        execution_target="local",
    )
    snapshot_id = new_id()
    content_hash = "a" * 64

    task.bind_memory_snapshot(snapshot_id, content_hash)
    task.bind_memory_snapshot(snapshot_id, content_hash)

    assert task.memory_snapshot_id == snapshot_id
    assert task.memory_snapshot_hash == content_hash
    with pytest.raises(InvalidTransitionError, match="already bound"):
        task.bind_memory_snapshot(new_id(), "b" * 64)
    with pytest.raises(ValueError, match="SHA-256"):
        Task.create(
            project_id=None,
            conversation_id=new_id(),
            user_request="Scratch",
            operation_mode=OperationMode.CREATE_NEW_VERSION,
            base_version_id=None,
            execution_target="local",
        ).bind_memory_snapshot(new_id(), "A" * 64)

    with pytest.raises(ValueError, match="both present"):
        replace(task, memory_snapshot_hash=None)


def test_scope_contract_is_immutable_and_digest_changes_with_scope(tmp_path: Path) -> None:
    ids = [new_id() for _ in range(6)]
    first = ScopeContract.create(
        workspace_type=WorkspaceType.PROJECT_CHAT,
        project_id=ids[0],
        conversation_id=ids[1],
        task_id=ids[2],
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=ids[3],
        target_version_id=ids[4],
        project_root=tmp_path / "version-a",
        allowed_write_paths=(tmp_path / "version-a",),
        forbidden_write_paths=(tmp_path / "version-b",),
        execution_target="local",
        network_policy="project_safe",
        memory_read_scope=("project_canonical", "current_conversation"),
        memory_write_scope=("current_conversation_draft",),
    )
    second = ScopeContract.create(
        workspace_type=WorkspaceType.PROJECT_CHAT,
        project_id=ids[0],
        conversation_id=ids[1],
        task_id=ids[2],
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=ids[3],
        target_version_id=ids[5],
        project_root=tmp_path / "version-c",
        allowed_write_paths=(tmp_path / "version-c",),
        forbidden_write_paths=(tmp_path / "version-b",),
        execution_target="local",
        network_policy="project_safe",
        memory_read_scope=("project_canonical", "current_conversation"),
        memory_write_scope=("current_conversation_draft",),
    )

    assert first.scope_digest != second.scope_digest
    with pytest.raises(FrozenInstanceError):
        first.execution_target = "cloud"  # type: ignore[misc]


def test_scope_digest_includes_memory_snapshot_binding(tmp_path: Path) -> None:
    common = {
        "workspace_type": WorkspaceType.CHAT_SCRATCH,
        "project_id": None,
        "conversation_id": new_id(),
        "task_id": new_id(),
        "operation_mode": OperationMode.CREATE_NEW_VERSION,
        "base_version_id": None,
        "target_version_id": None,
        "project_root": tmp_path / "scratch",
        "allowed_write_paths": (tmp_path / "scratch",),
        "forbidden_write_paths": (),
        "execution_target": "local",
        "network_policy": "off",
        "memory_read_scope": ("current_conversation",),
        "memory_write_scope": ("current_conversation_draft",),
    }
    unbound = ScopeContract.create(**common)
    bound = ScopeContract.create(
        **common,
        memory_snapshot_id=new_id(),
        memory_snapshot_hash="c" * 64,
    )

    assert unbound.scope_digest != bound.scope_digest
    assert bound.memory_snapshot_hash == "c" * 64
    with pytest.raises(ValueError, match="both present"):
        ScopeContract.create(**common, memory_snapshot_id=new_id())


def test_accept_version_uses_project_revision_compare_and_swap() -> None:
    project = Project.create(name="Example", residency=ProjectResidency.SYNCED)
    version_a = new_id()
    version_b = new_id()

    project.accept_version(version_a, expected_revision=0)

    with pytest.raises(VersionConflictError):
        project.accept_version(version_b, expected_revision=0)

    assert project.active_version_id == version_a
    assert project.revision == 1
