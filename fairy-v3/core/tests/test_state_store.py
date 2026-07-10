from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from fairy_core.domain.errors import VersionConflictError
from fairy_core.domain.execution import (
    Approval,
    ApprovalDecision,
    Changeset,
    ChangesetStatus,
    Checkpoint,
)
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import (
    Conversation,
    OperationMode,
    Project,
    ProjectResidency,
    Task,
    Version,
    VersionVisibility,
    WorkspaceType,
)
from fairy_core.storage import SqlAlchemyStateStore, SqliteStateStore


def test_sqlalchemy_state_store_isolates_tenants_and_idempotency_keys(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tenant_a = SqlAlchemyStateStore(
        engine,
        tenant_id="tenant-a",
        initialize_schema=True,
    )
    tenant_b = SqlAlchemyStateStore(engine, tenant_id="tenant-b")
    project_a = Project.create(name="Project A", residency=ProjectResidency.SYNCED)
    project_b = replace(project_a, name="Project B")
    conversation_a = Conversation.create(
        project_id=project_a.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=None,
    )
    conversation_b = Conversation.create(
        project_id=project_b.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=None,
    )
    task_a = Task.create(
        project_id=project_a.id,
        conversation_id=conversation_a.id,
        user_request="Tenant A",
        operation_mode=OperationMode.CREATE_NEW_VERSION,
        base_version_id=None,
        execution_target="cloud",
    )
    task_b = Task.create(
        project_id=project_b.id,
        conversation_id=conversation_b.id,
        user_request="Tenant B",
        operation_mode=OperationMode.CREATE_NEW_VERSION,
        base_version_id=None,
        execution_target="cloud",
    )

    tenant_a.save_project(project_a)
    tenant_a.save_conversation(conversation_a)
    tenant_a.save_task(task_a, idempotency_key="device:request-1")

    assert tenant_b.get_project(project_a.id) is None
    assert tenant_b.find_task_by_idempotency_key("device:request-1") is None

    tenant_b.save_project(project_b)
    tenant_b.save_conversation(conversation_b)
    tenant_b.save_task(task_b, idempotency_key="device:request-1")

    assert tenant_a.get_project(project_a.id).name == "Project A"
    assert tenant_b.get_project(project_b.id).name == "Project B"
    assert tenant_a.find_task_by_idempotency_key("device:request-1") == task_a
    assert tenant_b.find_task_by_idempotency_key("device:request-1") == task_b


def test_state_store_recovers_project_graph_after_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    store = SqliteStateStore(database_path)
    project = Project.create(name="Example", residency=ProjectResidency.LOCAL_ONLY)
    version = Version.create(
        project_id=project.id,
        source_conversation_id=None,
        source_task_id=None,
        parent_version_id=None,
        project_root=tmp_path / "versions" / "base",
        visibility=VersionVisibility.PROJECT_ACTIVE,
    )
    project.accept_version(version.id, expected_revision=0)
    conversation = Conversation.create(
        project_id=project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=version.id,
    )
    task = Task.create(
        project_id=project.id,
        conversation_id=conversation.id,
        user_request="Add pricing",
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=version.id,
        execution_target="local",
    )
    store.save_project(project)
    store.save_version(version)
    store.save_conversation(conversation)
    store.save_task(task, idempotency_key="device:request")
    store.close()

    restarted = SqliteStateStore(database_path)

    assert restarted.get_project(project.id) == project
    assert restarted.get_version(version.id) == version
    assert restarted.get_conversation(conversation.id) == conversation
    assert restarted.get_task(task.id) == task
    assert restarted.find_task_by_idempotency_key("device:request") == task


def test_accept_version_is_atomic_compare_and_swap(tmp_path: Path) -> None:
    store = SqliteStateStore(tmp_path / "state.db")
    project = Project.create(name="Synced", residency=ProjectResidency.SYNCED)
    store.save_project(project)
    first_version = Version.create(
        project_id=project.id,
        source_conversation_id=None,
        source_task_id=None,
        parent_version_id=None,
        project_root=tmp_path / "first",
        visibility=VersionVisibility.PROJECT_CANDIDATE,
    )
    second_version = Version.create(
        project_id=project.id,
        source_conversation_id=None,
        source_task_id=None,
        parent_version_id=None,
        project_root=tmp_path / "second",
        visibility=VersionVisibility.PROJECT_CANDIDATE,
    )
    store.save_version(first_version)
    store.save_version(second_version)

    accepted = store.accept_version(
        project_id=project.id,
        version_id=first_version.id,
        expected_revision=0,
    )
    with pytest.raises(VersionConflictError):
        store.accept_version(
            project_id=project.id,
            version_id=second_version.id,
            expected_revision=0,
        )

    assert accepted.active_version_id == first_version.id
    assert store.get_project(project.id).active_version_id == first_version.id
    assert store.get_project(project.id).revision == 1


def test_new_project_conversation_never_inherits_another_draft(tmp_path: Path) -> None:
    store = SqliteStateStore(tmp_path / "state.db")
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
    first = Conversation.create(
        project_id=project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=base.id,
    )
    first.active_draft_version_id = base.id
    second = Conversation.create(
        project_id=project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=project.active_version_id,
    )
    store.save_project(project)
    store.save_version(base)
    store.save_conversation(first)
    store.save_conversation(second)

    recovered = store.get_conversation(second.id)

    assert recovered.base_version_id == base.id
    assert recovered.active_draft_version_id is None
    assert recovered.active_preview_id is None


def test_execution_records_survive_restart_with_explicit_ownership(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    store = SqliteStateStore(database_path)
    project_id = new_id()
    conversation_id = new_id()
    task_id = new_id()
    version_id = new_id()
    changeset = Changeset.create(
        project_id=project_id,
        conversation_id=conversation_id,
        task_id=task_id,
        version_id=version_id,
        files=("README.md",),
        patches=("updated",),
        reason="Update readme",
        risk_level="medium",
        idempotency_key="changeset:state",
    )
    changeset.transition_to(ChangesetStatus.AWAITING_APPROVAL)
    approval = Approval.create(
        task_id=task_id,
        command_run_id=new_id(),
        changeset_id=changeset.id,
        requested_by="agent",
        reason="Write README.md",
    )
    approval.decide(decision=ApprovalDecision.APPROVED, decided_by="user")
    checkpoint = Checkpoint.create(
        task_id=task_id,
        version_id=version_id,
        changed_files=("README.md",),
        command_run_ids=(approval.command_run_id,),
        preview_artifact_id=None,
    )

    store.save_changeset(changeset)
    store.save_approval(approval)
    store.save_checkpoint(checkpoint)
    store.close()
    restarted = SqliteStateStore(database_path)

    assert restarted.get_changeset(changeset.id) == changeset
    assert restarted.find_changeset_by_idempotency_key("changeset:state") == changeset
    assert restarted.get_approval(approval.id) == approval
    assert restarted.get_checkpoint(checkpoint.id) == checkpoint
