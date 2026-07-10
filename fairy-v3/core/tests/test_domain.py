from __future__ import annotations

from dataclasses import FrozenInstanceError
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


def test_accept_version_uses_project_revision_compare_and_swap() -> None:
    project = Project.create(name="Example", residency=ProjectResidency.SYNCED)
    version_a = new_id()
    version_b = new_id()

    project.accept_version(version_a, expected_revision=0)

    with pytest.raises(VersionConflictError):
        project.accept_version(version_b, expected_revision=0)

    assert project.active_version_id == version_a
    assert project.revision == 1
