from __future__ import annotations

from pathlib import Path

import pytest

from fairy_core.application.core import CoreApplication
from fairy_core.application.errors import ApprovalRequiredError
from fairy_core.commanding.bus import CommandBus
from fairy_core.commanding.ledger import SqliteCommandLedger
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.models import ExecutionTarget, TaskCreate
from fairy_core.domain.models import (
    OperationMode,
    ProjectResidency,
    TaskStatus,
    VersionVisibility,
    WorkspaceType,
)
from fairy_core.storage import SqliteStateStore
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner


def _application(tmp_path: Path) -> CoreApplication:
    application, _ledger = _application_with_ledger(tmp_path)
    return application


def _application_with_ledger(tmp_path: Path) -> tuple[CoreApplication, SqliteCommandLedger]:
    registry = build_default_registry()
    ledger = SqliteCommandLedger(tmp_path / "ledger.db")
    return CoreApplication(
        state_store=SqliteStateStore(tmp_path / "state.db"),
        workspace_provisioner=FileSystemWorkspaceProvisioner(tmp_path / "managed"),
        command_bus=CommandBus(
            registry=registry,
            policy=PolicyEngine(registry),
            ledger=ledger,
        ),
    ), ledger


def test_project_task_creation_builds_isolated_version_and_scope(tmp_path: Path) -> None:
    app = _application(tmp_path)
    project_context = app.create_project(
        name="Example",
        residency=ProjectResidency.LOCAL_ONLY,
    )
    conversation = app.create_conversation(
        project_id=project_context.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    task_context = app.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Add pricing",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="device:request-1",
        )
    )

    assert project_context.initial_version.visibility is VersionVisibility.PROJECT_ACTIVE
    assert task_context.task.status is TaskStatus.PLANNING
    assert task_context.target_version.parent_version_id == project_context.initial_version.id
    assert task_context.target_version.project_root != project_context.initial_version.project_root
    assert task_context.target_version.project_root.is_dir()
    assert task_context.scope.task_id == task_context.task.id
    assert task_context.scope.target_version_id == task_context.target_version.id
    assert task_context.scope.allowed_write_paths == (task_context.target_version.project_root,)


def test_task_create_is_idempotent_and_does_not_fork_twice(tmp_path: Path) -> None:
    app = _application(tmp_path)
    project = app.create_project(name="Example", residency=ProjectResidency.LOCAL_ONLY)
    conversation = app.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    request = TaskCreate(
        conversation_id=conversation.id,
        user_request="Add pricing",
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        execution_target=ExecutionTarget.LOCAL,
        idempotency_key="same-request",
    )

    first = app.create_task(request)
    second = app.create_task(request)

    assert second.task.id == first.task.id
    assert second.target_version.id == first.target_version.id
    assert second.scope.scope_digest == first.scope.scope_digest


def test_accepting_task_version_requires_explicit_confirmation(tmp_path: Path) -> None:
    app = _application(tmp_path)
    project = app.create_project(name="Example", residency=ProjectResidency.LOCAL_ONLY)
    conversation = app.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    task = app.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Add pricing",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="accept-request",
        )
    )

    with pytest.raises(ApprovalRequiredError):
        app.accept_task_version(
            task_id=task.task.id,
            expected_project_revision=project.project.revision,
            user_confirmed=False,
        )

    for status in (
        TaskStatus.EXECUTING,
        TaskStatus.REVIEWING,
        TaskStatus.READY,
    ):
        app.transition_task(task.task.id, status)

    accepted = app.accept_task_version(
        task_id=task.task.id,
        expected_project_revision=project.project.revision,
        user_confirmed=True,
    )

    assert accepted.active_version_id == task.target_version.id
    assert accepted.revision == project.project.revision + 1


def test_scratch_task_receives_only_conversation_scratch_scope(tmp_path: Path) -> None:
    app = _application(tmp_path)
    conversation = app.create_conversation(
        project_id=None,
        workspace_type=WorkspaceType.CHAT_SCRATCH,
    )

    task = app.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Summarize these notes",
            operation_mode=OperationMode.ANSWER,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="scratch-request",
        )
    )

    assert task.task.project_id is None
    assert task.target_version is None
    assert task.scope.workspace_type is WorkspaceType.CHAT_SCRATCH
    assert task.scope.memory_write_scope == ("current_conversation_draft",)
    assert "scratch" in str(task.scope.project_root)


def test_project_and_task_workspace_side_effects_are_durable_commands(tmp_path: Path) -> None:
    app, ledger = _application_with_ledger(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("original", encoding="utf-8")

    project = app.create_project(
        name="Imported",
        residency=ProjectResidency.LOCAL_ONLY,
        source=source,
    )
    conversation = app.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    task = app.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Change readme",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="durable-workspace",
        )
    )

    command_names = [
        event.payload["command_name"]
        for event in ledger.events_after(cursor=0)
        if event.event_type == "command.created"
    ]
    assert command_names == ["workspace.import", "workspace.fork"]
    assert (source / "README.md").read_text(encoding="utf-8") == "original"
    assert (project.initial_version.project_root / "README.md").read_text(
        encoding="utf-8"
    ) == "original"
    assert task.scope.scope_digest
