from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from fairy_core.application.core import CoreApplication
from fairy_core.application.errors import ApprovalRequiredError
from fairy_core.assistant.models import (
    ImportedMessage,
    Message,
    MessageRole,
    MessageVisibility,
)
from fairy_core.commanding import CommandStatus, SqlAlchemyCommandLedger
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.models import (
    ChangesetProposal,
    ExecutionTarget,
    FileMutation,
    TaskCreate,
)
from fairy_core.domain.errors import (
    IdempotencyConflictError,
    InvalidTransitionError,
    ProjectBusyError,
    VersionConflictError,
)
from fairy_core.domain.execution import (
    ApprovalDecision,
    ChangesetStatus,
    PreviewSession,
    PreviewVisibility,
    RuntimeKind,
    RuntimeSession,
)
from fairy_core.domain.models import (
    OperationMode,
    ProjectResidency,
    TaskStatus,
    VersionVisibility,
    WorkspaceType,
)
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.storage.sqlalchemy import SqlAlchemyStateStore
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner


def _application(tmp_path: Path) -> CoreApplication:
    application, _ledger = _application_with_ledger(tmp_path)
    return application


def _build_application(
    tmp_path: Path,
    workspace: FileSystemWorkspaceProvisioner,
) -> tuple[
    CoreApplication,
    SqlAlchemyCommandLedger,
    SqlAlchemyUnitOfWorkFactory,
]:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    registry = build_default_registry()
    ledger = SqlAlchemyCommandLedger(engine, tenant_id="local")
    return (
        CoreApplication(
            unit_of_work_factory=factory,
            workspace_provisioner=workspace,
            registry=registry,
            policy=PolicyEngine(registry),
        ),
        ledger,
        factory,
    )


def _application_with_ledger(
    tmp_path: Path,
) -> tuple[CoreApplication, SqlAlchemyCommandLedger]:
    application, ledger, _factory = _build_application(
        tmp_path,
        FileSystemWorkspaceProvisioner(tmp_path / "managed"),
    )
    return application, ledger


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
    assert task_context.task.memory_snapshot_id is not None
    assert task_context.task.memory_snapshot_hash is not None
    assert task_context.scope.memory_snapshot_id == task_context.task.memory_snapshot_id
    assert task_context.scope.memory_snapshot_hash == task_context.task.memory_snapshot_hash


def test_scratch_conversation_moves_to_project_as_immutable_transcript(
    tmp_path: Path,
) -> None:
    app, _ledger, factory = _build_application(
        tmp_path,
        FileSystemWorkspaceProvisioner(tmp_path / "managed"),
    )
    project = app.create_project(name="Atlas", residency=ProjectResidency.LOCAL_ONLY)
    scratch = app.create_conversation(
        project_id=None,
        workspace_type=WorkspaceType.CHAT_SCRATCH,
    )
    scratch = app.update_conversation_metadata(
        conversation_id=scratch.id,
        title="Research notes",
        pinned=True,
        expected_revision=0,
    )
    task_context = app.create_task(
        TaskCreate(
            conversation_id=scratch.id,
            user_request="Compare the options",
            operation_mode=OperationMode.ANSWER,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="scratch-task",
        )
    )
    with factory() as unit_of_work:
        task = unit_of_work.state.get_task(task_context.task.id)
        assert task is not None
        task.status = TaskStatus.FAILED
        unit_of_work.state.save_task(task)
        for role, content in (
            (MessageRole.USER, "Compare the options"),
            (MessageRole.ASSISTANT, "Option A is safer."),
        ):
            unit_of_work.assistant.append_message(
                Message.create(
                    conversation_id=scratch.id,
                    task_id=task.id,
                    turn_id=None,
                    sequence=unit_of_work.assistant.next_message_sequence(scratch.id),
                    role=role,
                    visibility=MessageVisibility.USER,
                    content=content,
                )
            )
        unit_of_work.commit()

    moved = app.move_conversation_to_project(
        conversation_id=scratch.id,
        target_project_id=project.project.id,
        expected_revision=scratch.revision,
        user_confirmed=True,
        idempotency_key="move:research-notes",
    )
    replayed = app.move_conversation_to_project(
        conversation_id=scratch.id,
        target_project_id=project.project.id,
        expected_revision=scratch.revision,
        user_confirmed=True,
        idempotency_key="move:research-notes",
    )

    assert moved.imported_count == 2
    assert moved.source_conversation.deleted_at is not None
    assert moved.destination_conversation.project_id == project.project.id
    assert moved.destination_conversation.title == "Research notes"
    assert replayed.destination_conversation.id == moved.destination_conversation.id
    with factory() as unit_of_work:
        transcript = unit_of_work.assistant.list_transcript(
            conversation_id=moved.destination_conversation.id,
            limit=100,
            cursor=None,
            allowed_visibilities=frozenset({MessageVisibility.USER}),
        )
        visible = unit_of_work.state.list_conversations(
            project_id=None,
            limit=100,
            cursor=None,
        )
    assert [item.content for item in transcript.items] == [
        "Compare the options",
        "Option A is safer.",
    ]
    assert all(isinstance(item, ImportedMessage) for item in transcript.items)
    assert all(len(item.source_hash) == 64 for item in transcript.items)
    assert scratch.id not in {item.id for item in visible.items}
    with pytest.raises(VersionConflictError):
        app.update_conversation_metadata(
            conversation_id=moved.destination_conversation.id,
            title="Stale title",
            pinned=None,
            expected_revision=99,
        )


def test_project_lifecycle_cascades_only_project_deleted_conversations(
    tmp_path: Path,
) -> None:
    app, _ledger, factory = _build_application(
        tmp_path,
        FileSystemWorkspaceProvisioner(tmp_path / "managed"),
    )
    context = app.create_project(name="Atlas", residency=ProjectResidency.LOCAL_ONLY)
    project = context.project
    with factory() as unit_of_work:
        setup_conversation = unit_of_work.state.list_conversations(
            project_id=project.id,
            limit=100,
            cursor=None,
        ).items[0]
    individually_deleted = app.create_conversation(
        project_id=project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    individually_deleted = app.history.delete_conversation(
        conversation_id=individually_deleted.id,
        expected_revision=individually_deleted.revision,
        user_confirmed=True,
    )

    archived = app.history.archive_project(
        project_id=project.id,
        expected_revision=project.metadata_revision,
    )
    deleted = app.history.delete_project(
        project_id=project.id,
        expected_revision=archived.metadata_revision,
        user_confirmed=True,
    )
    restored = app.history.restore_deleted_project(
        project_id=project.id,
        expected_revision=deleted.metadata_revision,
    )

    assert restored.archived_at is not None
    assert restored.deleted_at is None
    with factory() as unit_of_work:
        restored_setup = unit_of_work.state.get_conversation(setup_conversation.id)
        still_deleted = unit_of_work.state.get_conversation(individually_deleted.id)
    assert restored_setup is not None
    assert restored_setup.deleted_at is None
    assert still_deleted is not None
    assert still_deleted.deleted_at is not None
    assert still_deleted.deleted_by_project_at is None


def test_project_archive_rejects_active_task(tmp_path: Path) -> None:
    app = _application(tmp_path)
    context = app.create_project(name="Busy", residency=ProjectResidency.LOCAL_ONLY)
    conversation = app.create_conversation(
        project_id=context.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    app.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Keep working",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="busy-project-task",
        )
    )

    with pytest.raises(ProjectBusyError) as error:
        app.history.archive_project(
            project_id=context.project.id,
            expected_revision=context.project.metadata_revision,
        )
    assert error.value.code == "PROJECT_BUSY"


def test_project_archive_rejects_ready_task_with_live_preview(tmp_path: Path) -> None:
    app, _ledger, factory = _build_application(
        tmp_path,
        FileSystemWorkspaceProvisioner(tmp_path / "managed"),
    )
    context = app.create_project(name="Preview busy", residency=ProjectResidency.LOCAL_ONLY)
    conversation = app.create_conversation(
        project_id=context.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    task_context = app.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Keep the Preview running",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="busy-project-preview",
        )
    )
    task = task_context.task
    task.transition_to(TaskStatus.EXECUTING)
    task.transition_to(TaskStatus.PREVIEWING)
    task.transition_to(TaskStatus.REVIEWING)
    task.transition_to(TaskStatus.READY)
    runtime = RuntimeSession.create(
        scope=task_context.scope,
        kind=RuntimeKind.STATIC_SITE,
        executor="test",
        idempotency_key="busy-project-preview:runtime",
    )
    runtime.begin_start()
    runtime.mark_running(executor_handle="preview-handle", port=43125)
    preview = PreviewSession.create(
        scope=task_context.scope,
        runtime_id=runtime.id,
        visibility=PreviewVisibility.PROJECT_ACTIVE,
        idempotency_key="busy-project-preview:preview",
    )
    preview.begin_start()
    preview.mark_ready("http://127.0.0.1:43125/")
    with factory() as unit_of_work:
        unit_of_work.state.save_task(task)
        unit_of_work.state.append_runtime(runtime)
        unit_of_work.state.append_preview(preview)
        unit_of_work.commit()

    with pytest.raises(ProjectBusyError, match="Preview"):
        app.history.archive_project(
            project_id=context.project.id,
            expected_revision=context.project.metadata_revision,
        )


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
    assert second.task.memory_snapshot_id == first.task.memory_snapshot_id
    assert second.task.memory_snapshot_hash == first.task.memory_snapshot_hash
    assert second.scope.scope_digest == first.scope.scope_digest


def test_task_idempotency_key_rejects_a_different_request(tmp_path: Path) -> None:
    app = _application(tmp_path)
    project = app.create_project(name="Example", residency=ProjectResidency.LOCAL_ONLY)
    conversation = app.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    first = TaskCreate(
        conversation_id=conversation.id,
        user_request="Add pricing",
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        execution_target=ExecutionTarget.LOCAL,
        idempotency_key="same-task-key",
    )
    app.create_task(first)

    with pytest.raises(IdempotencyConflictError):
        app.create_task(
            TaskCreate(
                conversation_id=conversation.id,
                user_request="Delete pricing",
                operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
                execution_target=ExecutionTarget.LOCAL,
                idempotency_key="same-task-key",
            )
        )


def test_task_idempotency_key_is_normalized_before_persistence(tmp_path: Path) -> None:
    app = _application(tmp_path)
    project = app.create_project(name="Example", residency=ProjectResidency.LOCAL_ONLY)
    conversation = app.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    first = app.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Add pricing",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="  normalized-task-key  ",
        )
    )
    replayed = app.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Add pricing",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="normalized-task-key",
        )
    )

    assert replayed.task.id == first.task.id


def test_concurrent_task_replays_converge_on_one_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        idempotency_key="concurrent-task-key",
    )
    barrier = threading.Barrier(2)
    original = SqlAlchemyStateStore.find_task_by_idempotency_key

    def synchronized_find(
        store: SqlAlchemyStateStore,
        idempotency_key: str,
    ):
        existing = original(store, idempotency_key)
        if idempotency_key == request.idempotency_key and existing is None:
            barrier.wait(timeout=5)
        return existing

    monkeypatch.setattr(
        SqlAlchemyStateStore,
        "find_task_by_idempotency_key",
        synchronized_find,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(app.create_task, request) for _index in range(2)]
        results = [future.result(timeout=10) for future in futures]

    assert results[0].task.id == results[1].task.id
    assert results[0].target_version.id == results[1].target_version.id


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


def test_discard_reservation_blocks_concurrent_version_accept(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = FileSystemWorkspaceProvisioner(tmp_path / "managed")
    app, _ledger, _factory = _build_application(tmp_path, workspace)
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
            idempotency_key="discard-race-task",
        )
    )
    for status in (TaskStatus.EXECUTING, TaskStatus.REVIEWING, TaskStatus.READY):
        app.transition_task(task.task.id, status)
    original_discard = workspace.discard_version
    accept_was_blocked = False

    def racing_discard(**kwargs: object) -> None:
        nonlocal accept_was_blocked
        try:
            app.accept_task_version(
                task_id=task.task.id,
                expected_project_revision=project.project.revision,
                user_confirmed=True,
            )
        except InvalidTransitionError:
            accept_was_blocked = True
        original_discard(**kwargs)

    monkeypatch.setattr(workspace, "discard_version", racing_discard)

    discarded = app.discard_task_version(task.task.id)
    persisted_project = app.get_project(project.project.id)

    assert accept_was_blocked is True
    assert discarded.status is TaskStatus.REJECTED
    assert persisted_project.active_version_id == project.initial_version.id
    assert not task.target_version.project_root.exists()


def test_failed_discard_can_be_retried_with_a_new_fenced_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = FileSystemWorkspaceProvisioner(tmp_path / "managed")
    app, _ledger, factory = _build_application(tmp_path, workspace)
    project = app.create_project(name="Example", residency=ProjectResidency.LOCAL_ONLY)
    conversation = app.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    task = app.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Discard this draft",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="discard-retry-task",
        )
    )
    original_discard = workspace.discard_version
    attempts = 0

    def fail_once(**kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("transient delete failure")
        original_discard(**kwargs)

    monkeypatch.setattr(workspace, "discard_version", fail_once)

    with pytest.raises(OSError, match="transient delete failure"):
        app.discard_task_version(task.task.id)

    retried = app.discard_task_version(task.task.id)
    with factory() as unit_of_work:
        discard_run_ids = [
            event.run_id
            for event in unit_of_work.commands.events_after(cursor=0)
            if event.event_type == "command.created"
            and event.payload["command_name"] == "workspace.discard"
        ]
        discard_runs = [
            unit_of_work.commands.get_run(run_id)
            for run_id in discard_run_ids
            if run_id is not None
        ]

    assert retried.status is TaskStatus.REJECTED
    assert not task.target_version.project_root.exists()
    assert len(discard_runs) == 2
    assert {run.status for run in discard_runs} == {
        CommandStatus.FAILED,
        CommandStatus.SUCCEEDED,
    }


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
    assert task.target_version is not None
    assert task.target_version.workspace_id == conversation.workspace_id
    assert task.scope.workspace_id == conversation.workspace_id
    assert task.scope.workspace_type is WorkspaceType.CHAT_SCRATCH
    assert task.scope.memory_write_scope == ("current_conversation_draft",)
    assert str(conversation.workspace_id) in str(task.scope.project_root)


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
    assert command_names == [
        "workspace.import",
        "memory.snapshot.build",
        "workspace.fork",
    ]
    fork_created = next(
        event
        for event in ledger.events_after(cursor=0)
        if event.event_type == "command.created"
        and event.payload.get("command_name") == "workspace.fork"
    )
    fork_run = ledger.get_run(fork_created.run_id)
    assert fork_run is not None
    assert fork_run.scope_digest == task.scope.scope_digest
    assert (source / "README.md").read_text(encoding="utf-8") == "original"
    assert (project.initial_version.project_root / "README.md").read_text(
        encoding="utf-8"
    ) == "original"
    assert task.scope.scope_digest


def test_changeset_idempotency_key_rejects_different_mutations(tmp_path: Path) -> None:
    app = _application(tmp_path)
    project = app.create_project(name="Example", residency=ProjectResidency.LOCAL_ONLY)
    conversation = app.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    task = app.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Update readme",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="changeset-task",
        )
    )
    app.propose_changeset(
        ChangesetProposal(
            task_id=task.task.id,
            files=(FileMutation(path="README.md", content="first"),),
            reason="Update copy",
            idempotency_key="same-changeset-key",
        )
    )

    with pytest.raises(IdempotencyConflictError):
        app.propose_changeset(
            ChangesetProposal(
                task_id=task.task.id,
                files=(FileMutation(path="README.md", content="different"),),
                reason="Update copy",
                idempotency_key="same-changeset-key",
            )
        )


def test_changeset_rejects_stale_workspace_revision(tmp_path: Path) -> None:
    app = _application(tmp_path)
    project = app.create_project(name="Example", residency=ProjectResidency.LOCAL_ONLY)
    conversation = app.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    task = app.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Update readme",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="stale-workspace-task",
        )
    )

    with pytest.raises(VersionConflictError, match="expected Workspace revision"):
        app.propose_changeset(
            ChangesetProposal(
                task_id=task.task.id,
                files=(FileMutation(path="README.md", content="stale"),),
                expected_workspace_revision=999,
                reason="Reject a stale write",
                idempotency_key="stale-workspace-changeset",
            )
        )


def test_changeset_idempotency_key_is_normalized_before_replay(tmp_path: Path) -> None:
    app = _application(tmp_path)
    project = app.create_project(name="Example", residency=ProjectResidency.LOCAL_ONLY)
    conversation = app.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    task = app.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Update readme",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="normalized-changeset-task",
        )
    )
    first = app.propose_changeset(
        ChangesetProposal(
            task_id=task.task.id,
            files=(FileMutation(path="README.md", content="draft"),),
            reason="Update copy",
            idempotency_key="  normalized-changeset-key  ",
        )
    )
    replayed = app.propose_changeset(
        ChangesetProposal(
            task_id=task.task.id,
            files=(FileMutation(path="README.md", content="draft"),),
            reason="Update copy",
            idempotency_key="normalized-changeset-key",
        )
    )

    assert replayed.changeset.id == first.changeset.id
    assert replayed.approval.id == first.approval.id


def test_concurrent_changeset_replays_converge_on_one_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _application(tmp_path)
    project = app.create_project(name="Example", residency=ProjectResidency.LOCAL_ONLY)
    conversation = app.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    task = app.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Update readme",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="concurrent-changeset-task",
        )
    )
    request = ChangesetProposal(
        task_id=task.task.id,
        files=(FileMutation(path="README.md", content="draft"),),
        reason="Update copy",
        idempotency_key="concurrent-changeset-key",
    )
    barrier = threading.Barrier(2)
    original = SqlAlchemyStateStore.find_changeset_by_idempotency_key

    def synchronized_find(
        store: SqlAlchemyStateStore,
        idempotency_key: str,
    ):
        existing = original(store, idempotency_key)
        if idempotency_key == request.idempotency_key and existing is None:
            barrier.wait(timeout=5)
        return existing

    monkeypatch.setattr(
        SqlAlchemyStateStore,
        "find_changeset_by_idempotency_key",
        synchronized_find,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(app.propose_changeset, request) for _index in range(2)]
        results = [future.result(timeout=10) for future in futures]

    assert results[0].changeset.id == results[1].changeset.id
    assert results[0].approval.id == results[1].approval.id


class _CrashBeforeForkWorkspace(FileSystemWorkspaceProvisioner):
    def __init__(self, managed_root: Path) -> None:
        super().__init__(managed_root)
        self.crash_before_next_fork = True

    def fork_version(self, **kwargs: object) -> Path:
        if self.crash_before_next_fork:
            self.crash_before_next_fork = False
            raise KeyboardInterrupt("simulated process crash")
        return super().fork_version(**kwargs)


class _CrashBeforeWriteWorkspace(FileSystemWorkspaceProvisioner):
    def __init__(self, managed_root: Path) -> None:
        super().__init__(managed_root)
        self.crash_before_next_write = True

    def write_text(self, **kwargs: object) -> Path:
        if self.crash_before_next_write:
            self.crash_before_next_write = False
            raise KeyboardInterrupt("simulated process crash")
        return super().write_text(**kwargs)


class _FailSecondWriteWorkspace(FileSystemWorkspaceProvisioner):
    def __init__(self, managed_root: Path) -> None:
        super().__init__(managed_root)
        self.write_count = 0

    def write_text(self, **kwargs: object) -> Path:
        self.write_count += 1
        if self.write_count == 2:
            raise OSError("second write failed")
        return super().write_text(**kwargs)


def test_task_replay_after_intent_commit_does_not_duplicate_workspace_command(
    tmp_path: Path,
) -> None:
    workspace = _CrashBeforeForkWorkspace(tmp_path / "managed")
    app, _ledger, factory = _build_application(
        tmp_path,
        workspace,
    )
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
        idempotency_key="crash-replay",
    )

    with pytest.raises(KeyboardInterrupt, match="simulated process crash"):
        app.create_task(request)

    replayed = app.create_task(request)

    with factory() as unit_of_work:
        persisted = unit_of_work.state.find_task_by_idempotency_key("crash-replay")
        assert persisted is not None
        target = unit_of_work.state.get_version(persisted.target_version_id)
        persisted_conversation = unit_of_work.state.get_conversation(conversation.id)
        command_names = [
            event.payload["command_name"]
            for event in unit_of_work.commands.events_after(cursor=0)
            if event.event_type == "command.created"
        ]

    assert replayed.task.id == persisted.id
    assert persisted.status is TaskStatus.RESOLVING_SCOPE
    assert target is not None
    assert target.source_task_id == persisted.id
    assert persisted_conversation.active_draft_version_id == target.id
    assert not target.project_root.exists()
    assert command_names.count("workspace.fork") == 1


def test_approval_replay_after_intent_commit_does_not_repeat_file_write(
    tmp_path: Path,
) -> None:
    workspace = _CrashBeforeWriteWorkspace(tmp_path / "managed")
    app, _ledger, factory = _build_application(tmp_path, workspace)
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("base", encoding="utf-8")
    project = app.create_project(
        name="Example",
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
            user_request="Update readme",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="approval-crash-task",
        )
    )
    pending = app.propose_changeset(
        ChangesetProposal(
            task_id=task.task.id,
            files=(FileMutation(path="README.md", content="draft"),),
            reason="Apply requested copy",
            idempotency_key="approval-crash-changeset",
        )
    )

    with pytest.raises(KeyboardInterrupt, match="simulated process crash"):
        app.decide_approval(
            approval_id=pending.approval.id,
            approved=True,
            decided_by="user",
        )

    replayed = app.decide_approval(
        approval_id=pending.approval.id,
        approved=True,
        decided_by="user",
    )

    with factory() as unit_of_work:
        approval = unit_of_work.state.get_approval(pending.approval.id)
        persisted_task = unit_of_work.state.get_task(task.task.id)
        run = unit_of_work.commands.get_run(pending.approval.command_run_id)
        command_names = [
            event.payload["command_name"]
            for event in unit_of_work.commands.events_after(cursor=0)
            if event.event_type == "command.created"
        ]

    assert replayed.status is ChangesetStatus.APPLYING
    assert approval.decision is ApprovalDecision.APPROVED
    assert persisted_task.status is TaskStatus.EXECUTING
    assert run.status is CommandStatus.RUNNING
    assert run.lease_owner is not None
    assert command_names.count("edit.apply_changeset") == 1
    assert (task.target_version.project_root / "README.md").read_text(encoding="utf-8") == "base"


def test_changeset_restores_all_files_when_a_later_write_fails(tmp_path: Path) -> None:
    workspace = _FailSecondWriteWorkspace(tmp_path / "managed")
    app, _ledger, _factory = _build_application(tmp_path, workspace)
    source = tmp_path / "source"
    source.mkdir()
    (source / "one.txt").write_text("one-base", encoding="utf-8")
    (source / "two.txt").write_text("two-base", encoding="utf-8")
    project = app.create_project(
        name="Example",
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
            user_request="Update both files",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="atomic-write-task",
        )
    )
    pending = app.propose_changeset(
        ChangesetProposal(
            task_id=task.task.id,
            files=(
                FileMutation(path="one.txt", content="one-draft"),
                FileMutation(path="two.txt", content="two-draft"),
            ),
            reason="Apply both file changes",
            idempotency_key="atomic-write-changeset",
        )
    )

    with pytest.raises(OSError, match="second write failed"):
        app.decide_approval(
            approval_id=pending.approval.id,
            approved=True,
            decided_by="user",
        )

    assert (task.target_version.project_root / "one.txt").read_text(encoding="utf-8") == "one-base"
    assert (task.target_version.project_root / "two.txt").read_text(encoding="utf-8") == "two-base"
