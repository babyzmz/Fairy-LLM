from __future__ import annotations

import shutil
from pathlib import Path
from uuid import UUID

from fairy_core.application.core import CoreApplication
from fairy_core.commanding import SqlAlchemyCommandLedger
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.models import (
    ChangesetProposal,
    ExecutionTarget,
    FileMutation,
    TaskCreate,
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


class FakeWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def version_path(self, project_id: UUID | str, version_id: UUID | str) -> Path:
        return self.root / "projects" / str(project_id) / "versions" / str(version_id)

    def create_initial_version(
        self,
        project_id: UUID | str,
        version_id: UUID | str,
        *,
        source: Path | None = None,
    ) -> Path:
        target = self.version_path(project_id, version_id)
        if source is None:
            target.mkdir(parents=True)
        else:
            shutil.copytree(source, target)
        return target.resolve()

    def fork_version(
        self,
        *,
        project_id: UUID | str,
        version_id: UUID | str,
        parent_version_id: UUID | str,
    ) -> Path:
        target = self.version_path(project_id, version_id)
        shutil.copytree(self.version_path(project_id, parent_version_id), target)
        return target.resolve()

    def create_scratch(self, conversation_id: UUID | str, task_id: UUID | str) -> Path:
        target = self.scratch_path(conversation_id, task_id)
        target.mkdir(parents=True, exist_ok=True)
        return target.resolve()

    def scratch_path(self, conversation_id: UUID | str, task_id: UUID | str) -> Path:
        return self.root / "scratch" / str(conversation_id) / str(task_id)

    def write_text(
        self,
        *,
        project_id: UUID | str,
        version_id: UUID | str,
        relative_path: str,
        content: str,
    ) -> Path:
        target = self.version_path(project_id, version_id) / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def apply_changeset(
        self,
        *,
        project_id: UUID | str,
        version_id: UUID | str,
        mutations: tuple[tuple[str, str], ...],
    ) -> tuple[Path, ...]:
        return tuple(
            self.write_text(
                project_id=project_id,
                version_id=version_id,
                relative_path=path,
                content=content,
            )
            for path, content in mutations
        )

    def diff(self, *, project_id: UUID | str, version_id: UUID | str) -> str:
        return "M README.md"

    def checkpoint(
        self,
        *,
        project_id: UUID | str,
        version_id: UUID | str,
        message: str,
    ) -> str:
        return "a" * 40

    def discard_version(self, *, project_id: UUID | str, version_id: UUID | str) -> None:
        shutil.rmtree(self.version_path(project_id, version_id))


def _application(tmp_path: Path) -> tuple[CoreApplication, SqlAlchemyCommandLedger]:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    registry = build_default_registry()
    ledger = SqlAlchemyCommandLedger(engine, tenant_id="local")
    return (
        CoreApplication(
            unit_of_work_factory=SqlAlchemyUnitOfWorkFactory(
                engine,
                tenant_id="local",
            ),
            workspace_provisioner=FakeWorkspace(tmp_path / "managed"),
            registry=registry,
            policy=PolicyEngine(registry),
        ),
        ledger,
    )


def test_preview_first_project_loop_keeps_draft_isolated_until_accept(tmp_path: Path) -> None:
    app, ledger = _application(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("base", encoding="utf-8")
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
            user_request="Update readme",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="loop:task-1",
        )
    )

    pending = app.propose_changeset(
        ChangesetProposal(
            task_id=task.task.id,
            files=(FileMutation(path="README.md", content="draft"),),
            reason="Apply requested copy",
            idempotency_key="loop:changeset-1",
        )
    )
    retried = app.propose_changeset(
        ChangesetProposal(
            task_id=task.task.id,
            files=(FileMutation(path="README.md", content="draft"),),
            reason="Apply requested copy",
            idempotency_key="loop:changeset-1",
        )
    )

    assert task.task.status is TaskStatus.PLANNING
    assert retried.changeset.id == pending.changeset.id
    assert retried.approval.id == pending.approval.id
    assert pending.changeset.status.value == "awaiting_approval"
    assert (project.initial_version.project_root / "README.md").read_text() == "base"
    assert (task.target_version.project_root / "README.md").read_text() == "base"
    assert app.get_project(project.project.id).active_version_id == project.initial_version.id

    applied = app.decide_approval(
        approval_id=pending.approval.id,
        approved=True,
        decided_by="user",
    )
    checkpoint = app.review_task(task.task.id)

    assert applied.status.value == "applied"
    assert (task.target_version.project_root / "README.md").read_text() == "draft"
    assert (project.initial_version.project_root / "README.md").read_text() == "base"
    assert (source / "README.md").read_text() == "base"
    assert checkpoint.changed_files == ("README.md",)
    assert app.get_task(task.task.id).status is TaskStatus.READY
    assert app.get_project(project.project.id).active_version_id == project.initial_version.id

    accepted = app.accept_task_version(
        task_id=task.task.id,
        expected_project_revision=project.project.revision,
        user_confirmed=True,
    )

    assert accepted.active_version_id == task.target_version.id
    assert app.get_version(task.target_version.id).visibility is VersionVisibility.PROJECT_ACTIVE
    command_names = [
        event.payload["command_name"]
        for event in ledger.events_after(cursor=0)
        if event.event_type == "command.created"
    ]
    assert command_names == [
        "workspace.import",
        "memory.snapshot.build",
        "workspace.fork",
        "edit.apply_changeset",
        "workspace.diff",
        "workspace.checkpoint",
        "project.accept_version",
    ]


def test_discard_removes_candidate_without_changing_active_version(tmp_path: Path) -> None:
    app, _ledger = _application(tmp_path)
    project = app.create_project(name="Empty", residency=ProjectResidency.LOCAL_ONLY)
    conversation = app.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    task = app.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Try another direction",
            operation_mode=OperationMode.CREATE_NEW_VERSION,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="loop:discard",
        )
    )

    app.discard_task_version(task.task.id)

    assert app.get_project(project.project.id).active_version_id == project.initial_version.id
    assert app.get_task(task.task.id).status is TaskStatus.REJECTED
    assert app.get_version(task.target_version.id).visibility is VersionVisibility.REJECTED
    assert not task.target_version.project_root.exists()


def test_discard_removes_a_failed_candidate_without_promoting_it(tmp_path: Path) -> None:
    app, _ledger = _application(tmp_path)
    project = app.create_project(name="Failed", residency=ProjectResidency.LOCAL_ONLY)
    conversation = app.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    task = app.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Fail safely",
            operation_mode=OperationMode.CREATE_NEW_VERSION,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="loop:failed-discard",
        )
    )
    app.transition_task(task.task.id, TaskStatus.FAILED)

    discarded = app.discard_task_version(task.task.id)

    assert discarded.status is TaskStatus.REJECTED
    assert app.get_project(project.project.id).active_version_id == project.initial_version.id
    assert app.get_version(task.target_version.id).visibility is VersionVisibility.REJECTED
    assert not task.target_version.project_root.exists()
