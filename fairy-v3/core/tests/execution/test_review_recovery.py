from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

from fairy_core.application.core import CoreApplication
from fairy_core.commanding import CommandStatus, EventVisibility
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.commanding.schema import command_runs
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
    WorkspaceType,
)
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner


class CrashAfterCheckpointWorkspace(FileSystemWorkspaceProvisioner):
    def __init__(self, managed_root: Path) -> None:
        super().__init__(managed_root)
        self.commits: list[str] = []
        self._crash = True

    def checkpoint(self, **values: object) -> str:
        commit = super().checkpoint(**values)
        self.commits.append(commit)
        if self._crash:
            self._crash = False
            raise KeyboardInterrupt("simulated crash after Git checkpoint")
        return commit


def test_review_reclaims_checkpoint_after_crash_without_duplicate_git_commit(
    tmp_path: Path,
) -> None:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    registry = build_default_registry()
    workspace = CrashAfterCheckpointWorkspace(tmp_path / "managed")
    application = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=workspace,
        registry=registry,
        policy=PolicyEngine(registry),
    )
    project = application.create_project(
        name="Review recovery",
        residency=ProjectResidency.LOCAL_ONLY,
    )
    conversation = application.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    task = application.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Update the readme",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="review:recovery:task",
        )
    )
    pending = application.propose_changeset(
        ChangesetProposal(
            task_id=task.task.id,
            files=(FileMutation(path="README.md", content="recovered"),),
            reason="Exercise Review recovery",
            idempotency_key="review:recovery:changeset",
        )
    )
    application.decide_approval(
        approval_id=pending.approval.id,
        approved=True,
        decided_by="user",
    )

    with pytest.raises(KeyboardInterrupt, match="simulated crash"):
        application.review_task(task.task.id)

    with engine.begin() as connection:
        connection.execute(
            update(command_runs)
            .where(
                command_runs.c.tenant_id == "local",
                command_runs.c.task_id == str(task.task.id),
                command_runs.c.command_name == "workspace.checkpoint",
                command_runs.c.status == CommandStatus.RUNNING.value,
            )
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )

    checkpoint = application.review_task(task.task.id)

    with factory() as unit_of_work:
        persisted_task = unit_of_work.state.get_task(task.task.id)
        events = unit_of_work.commands.events_after(
            cursor=0,
            allowed_visibilities={EventVisibility.USER},
        )
    assert persisted_task is not None and persisted_task.status is TaskStatus.READY
    assert checkpoint.task_id == task.task.id
    assert len(workspace.commits) == 2
    assert len(set(workspace.commits)) == 1
    assert [event.event_type for event in events].count("command.reclaimed") == 1
