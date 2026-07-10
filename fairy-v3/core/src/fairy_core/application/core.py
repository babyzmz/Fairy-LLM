from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from uuid import UUID

from fairy_core.application.errors import ApprovalRequiredError
from fairy_core.commanding import CommandStatus
from fairy_core.commanding.bus import CommandBus, CommandRequest
from fairy_core.commanding.types import PermissionProfile
from fairy_core.contracts.models import ChangesetProposal, TaskCreate
from fairy_core.domain.errors import InvalidTransitionError
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
    ScopeContract,
    Task,
    TaskStatus,
    Version,
    VersionVisibility,
    WorkspaceType,
)
from fairy_core.storage import StateStore
from fairy_core.workspace.ports import WorkspaceProvisioner

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ProjectContext:
    project: Project
    initial_version: Version


@dataclass(frozen=True, slots=True)
class TaskContext:
    task: Task
    target_version: Version | None
    scope: ScopeContract


@dataclass(frozen=True, slots=True)
class PendingChangeset:
    changeset: Changeset
    approval: Approval


class CoreApplication:
    def __init__(
        self,
        *,
        state_store: StateStore,
        workspace_provisioner: WorkspaceProvisioner,
        command_bus: CommandBus,
    ) -> None:
        self._state = state_store
        self._workspaces = workspace_provisioner
        self._commands = command_bus

    def create_project(
        self,
        *,
        name: str,
        residency: ProjectResidency,
        source: Path | None = None,
    ) -> ProjectContext:
        project = Project.create(name=name, residency=residency)
        version_id = new_id()
        setup_conversation = Conversation.create(
            project_id=project.id,
            workspace_type=WorkspaceType.PROJECT_CHAT,
            base_version_id=None,
        )
        setup_task = Task.create(
            project_id=project.id,
            conversation_id=setup_conversation.id,
            user_request="Import project" if source is not None else "Create project",
            operation_mode=OperationMode.CREATE_NEW_VERSION,
            base_version_id=None,
            execution_target="local",
        )
        setup_task.bind_target_version(version_id)
        setup_task.transition_to(TaskStatus.RESOLVING_SCOPE)
        root_hint = self._workspaces.version_path(project.id, version_id)
        scope = ScopeContract.create(
            workspace_type=WorkspaceType.PROJECT_CHAT,
            project_id=project.id,
            conversation_id=setup_conversation.id,
            task_id=setup_task.id,
            operation_mode=setup_task.operation_mode,
            base_version_id=None,
            target_version_id=version_id,
            project_root=root_hint,
            allowed_write_paths=(root_hint,),
            forbidden_write_paths=(),
            execution_target="local",
            network_policy="project_safe",
            memory_read_scope=(),
            memory_write_scope=("current_conversation_draft",),
        )
        self._state.save_project(project)
        self._state.save_conversation(setup_conversation)
        self._state.save_task(
            setup_task,
            idempotency_key=f"project:{project.id}:initialize",
        )
        tool_name = "workspace.import" if source is not None else "workspace.create_empty"
        payload: dict[str, object] = {
            "project_id": str(project.id),
            "version_id": str(version_id),
        }
        if source is not None:
            payload["source"] = str(source.resolve(strict=True))
        try:
            root = self._execute_command(
                tool_name=tool_name,
                scope=scope,
                payload=payload,
                idempotency_key=f"project:{project.id}:workspace",
                operation=lambda: self._workspaces.create_initial_version(
                    project.id,
                    version_id,
                    source=source,
                ),
                output=lambda path: {"root": str(path)},
            )
        except Exception:
            setup_task.transition_to(TaskStatus.FAILED)
            self._state.save_task(setup_task)
            raise
        version = Version.create(
            version_id=version_id,
            project_id=project.id,
            source_conversation_id=None,
            source_task_id=None,
            parent_version_id=None,
            project_root=root,
            visibility=VersionVisibility.PROJECT_ACTIVE,
        )
        project.accept_version(version.id, expected_revision=0)
        for status in (
            TaskStatus.BUILDING_WORKSPACE,
            TaskStatus.PLANNING,
            TaskStatus.EXECUTING,
            TaskStatus.REVIEWING,
            TaskStatus.READY,
            TaskStatus.ACCEPTED,
        ):
            setup_task.transition_to(status)
        setup_conversation.base_version_id = version.id
        self._state.save_version(version)
        self._state.save_project(project)
        self._state.save_task(setup_task)
        self._state.save_conversation(setup_conversation)
        return ProjectContext(project=project, initial_version=version)

    def create_conversation(
        self,
        *,
        project_id: UUID | None,
        workspace_type: WorkspaceType,
    ) -> Conversation:
        base_version_id = None
        if workspace_type is WorkspaceType.PROJECT_CHAT:
            if project_id is None:
                raise ValueError("project_chat requires project_id")
            project = self._state.get_project(project_id)
            if project is None:
                raise KeyError(f"project not found: {project_id}")
            base_version_id = project.active_version_id
        conversation = Conversation.create(
            project_id=project_id,
            workspace_type=workspace_type,
            base_version_id=base_version_id,
        )
        self._state.save_conversation(conversation)
        return conversation

    def create_task(self, request: TaskCreate) -> TaskContext:
        existing = self._state.find_task_by_idempotency_key(request.idempotency_key)
        if existing is not None:
            return self._context_for(existing)

        conversation = self._state.get_conversation(request.conversation_id)
        if conversation is None:
            raise KeyError(f"conversation not found: {request.conversation_id}")
        base_version_id = conversation.active_draft_version_id or conversation.base_version_id
        task = Task.create(
            project_id=conversation.project_id,
            conversation_id=conversation.id,
            user_request=request.user_request,
            operation_mode=request.operation_mode,
            base_version_id=base_version_id,
            execution_target=request.execution_target.value,
        )

        target_version: Version | None = None
        if conversation.workspace_type is WorkspaceType.PROJECT_CHAT:
            if conversation.project_id is None or base_version_id is None:
                raise ValueError("project conversation has no base version")
            parent = self._state.get_version(base_version_id)
            if parent is None:
                raise KeyError(f"base version not found: {base_version_id}")
            version_id = new_id()
            root_hint = self._workspaces.version_path(conversation.project_id, version_id)
            target_version = Version.create(
                version_id=version_id,
                project_id=conversation.project_id,
                source_conversation_id=conversation.id,
                source_task_id=task.id,
                parent_version_id=parent.id,
                project_root=root_hint,
                visibility=VersionVisibility.CHAT_DRAFT,
            )
            task.bind_target_version(target_version.id)
            conversation.active_draft_version_id = target_version.id
            self._state.save_version(target_version)

        task.transition_to(TaskStatus.RESOLVING_SCOPE)
        conversation.active_task_id = task.id
        self._state.save_task(task, idempotency_key=request.idempotency_key)
        self._state.save_conversation(conversation)
        context = self._build_context(task, conversation, target_version)
        try:
            if target_version is not None:
                assert task.project_id is not None and task.base_version_id is not None
                root = self._execute_command(
                    tool_name="workspace.fork",
                    scope=context.scope,
                    payload={
                        "project_id": str(task.project_id),
                        "parent_version_id": str(task.base_version_id),
                        "version_id": str(target_version.id),
                    },
                    idempotency_key=f"{request.idempotency_key}:workspace",
                    operation=lambda: self._workspaces.fork_version(
                        project_id=task.project_id,
                        version_id=target_version.id,
                        parent_version_id=task.base_version_id,
                    ),
                    output=lambda path: {"root": str(path)},
                )
                target_version.project_root = root.resolve(strict=False)
                self._state.save_version(target_version)
            else:
                self._execute_command(
                    tool_name="workspace.create_scratch",
                    scope=context.scope,
                    payload={
                        "conversation_id": str(conversation.id),
                        "task_id": str(task.id),
                    },
                    idempotency_key=f"{request.idempotency_key}:workspace",
                    operation=lambda: self._workspaces.create_scratch(conversation.id, task.id),
                    output=lambda path: {"root": str(path)},
                )
        except Exception:
            task.transition_to(TaskStatus.FAILED)
            self._state.save_task(task)
            raise
        task.transition_to(TaskStatus.BUILDING_WORKSPACE)
        task.transition_to(TaskStatus.PLANNING)
        self._state.save_task(task)
        return self._build_context(task, conversation, target_version)

    def propose_changeset(self, request: ChangesetProposal) -> PendingChangeset:
        existing = self._state.find_changeset_by_idempotency_key(request.idempotency_key)
        if existing is not None:
            approval = self._state.find_approval_by_changeset_id(existing.id)
            if approval is None:
                raise RuntimeError("changeset approval is missing")
            return PendingChangeset(changeset=existing, approval=approval)
        task = self.get_task(request.task_id)
        if task.status is not TaskStatus.PLANNING:
            raise InvalidTransitionError("Task must be planning before proposing a Changeset")
        if task.project_id is None or task.target_version_id is None:
            raise ValueError("scratch tasks cannot apply project Changesets")
        context = self._context_for(task)
        changeset = Changeset.create(
            project_id=task.project_id,
            conversation_id=task.conversation_id,
            task_id=task.id,
            version_id=task.target_version_id,
            files=tuple(mutation.path for mutation in request.files),
            patches=tuple(mutation.content for mutation in request.files),
            reason=request.reason,
            risk_level="medium",
            idempotency_key=request.idempotency_key,
        )
        dispatch = self._commands.submit(
            CommandRequest(
                tool_name="edit.apply_changeset",
                actor="agent",
                scope=context.scope,
                payload={"files": list(changeset.files), "reason": changeset.reason},
                idempotency_key=f"{request.idempotency_key}:apply",
            ),
            profile=PermissionProfile.STANDARD,
            capability_overrides={},
            sandbox_healthy=False,
        )
        if not dispatch.accepted or not dispatch.requires_approval or dispatch.run is None:
            raise RuntimeError(dispatch.error_code or "Changeset approval command was rejected")
        changeset.transition_to(ChangesetStatus.AWAITING_APPROVAL)
        approval = Approval.create(
            task_id=task.id,
            command_run_id=dispatch.run.id,
            changeset_id=changeset.id,
            requested_by="agent",
            reason=f"Write {len(changeset.files)} project file(s)",
        )
        task.transition_to(TaskStatus.AWAITING_APPROVAL)
        self._state.save_changeset(changeset)
        self._state.save_approval(approval)
        self._state.save_task(task)
        return PendingChangeset(changeset=changeset, approval=approval)

    def decide_approval(
        self,
        *,
        approval_id: UUID,
        approved: bool,
        decided_by: str,
    ) -> Changeset:
        approval = self._state.get_approval(approval_id)
        if approval is None:
            raise KeyError(f"approval not found: {approval_id}")
        if approval.changeset_id is None:
            raise ValueError("approval is not associated with a Changeset")
        changeset = self._state.get_changeset(approval.changeset_id)
        if changeset is None:
            raise KeyError(f"changeset not found: {approval.changeset_id}")
        decision = ApprovalDecision.APPROVED if approved else ApprovalDecision.REJECTED
        if approval.decision is not ApprovalDecision.PENDING:
            if approval.decision is decision:
                return changeset
            raise InvalidTransitionError("approval has already been decided differently")
        task = self.get_task(approval.task_id)
        approval.decide(decision=decision, decided_by=decided_by)
        changeset.record_approval(decision)
        if not approved:
            self._commands.decide_approval(approval.command_run_id, approved=False)
            changeset.transition_to(ChangesetStatus.REJECTED)
            task.transition_to(TaskStatus.REJECTED)
            self._state.save_approval(approval)
            self._state.save_changeset(changeset)
            self._state.save_task(task)
            return changeset

        self._commands.decide_approval(approval.command_run_id, approved=True)
        self._commands.start(approval.command_run_id)
        changeset.transition_to(ChangesetStatus.APPLYING)
        task.transition_to(TaskStatus.EXECUTING)
        self._state.save_approval(approval)
        self._state.save_changeset(changeset)
        self._state.save_task(task)
        try:
            written = [
                self._workspaces.write_text(
                    project_id=changeset.project_id,
                    version_id=changeset.version_id,
                    relative_path=path,
                    content=content,
                )
                for path, content in zip(changeset.files, changeset.patches, strict=True)
            ]
        except Exception as error:
            self._commands.fail(
                approval.command_run_id,
                error_code=str(getattr(error, "error_code", "WORKER_INTERRUPTED")),
            )
            changeset.transition_to(ChangesetStatus.FAILED)
            task.transition_to(TaskStatus.FAILED)
            self._state.save_changeset(changeset)
            self._state.save_task(task)
            raise
        self._commands.complete(
            approval.command_run_id,
            output={"changed_files": [str(path) for path in written]},
        )
        changeset.transition_to(ChangesetStatus.APPLIED)
        self._state.save_changeset(changeset)
        return changeset

    def review_task(self, task_id: UUID) -> Checkpoint:
        task = self.get_task(task_id)
        if task.status is not TaskStatus.EXECUTING:
            raise InvalidTransitionError("Task must be executing before review")
        if task.project_id is None or task.target_version_id is None:
            raise ValueError("scratch tasks do not produce project checkpoints")
        task.transition_to(TaskStatus.REVIEWING)
        self._state.save_task(task)
        context = self._context_for(task)
        diff = self._execute_command(
            tool_name="workspace.diff",
            scope=context.scope,
            payload={
                "project_id": str(task.project_id),
                "version_id": str(task.target_version_id),
            },
            idempotency_key=f"task:{task.id}:diff",
            operation=lambda: self._workspaces.diff(
                project_id=task.project_id,
                version_id=task.target_version_id,
            ),
            output=lambda value: {"has_changes": bool(value), "bytes": len(value)},
        )
        commit = self._execute_command(
            tool_name="workspace.checkpoint",
            scope=context.scope,
            payload={
                "project_id": str(task.project_id),
                "version_id": str(task.target_version_id),
            },
            idempotency_key=f"task:{task.id}:checkpoint",
            operation=lambda: self._workspaces.checkpoint(
                project_id=task.project_id,
                version_id=task.target_version_id,
                message=f"Fairy Task {task.id}",
            ),
            output=lambda value: {"commit": value},
        )
        changesets = self._state.changesets_for_task(task.id)
        changed_files = tuple(dict.fromkeys(path for item in changesets for path in item.files))
        approvals = [
            approval
            for item in changesets
            if (approval := self._state.find_approval_by_changeset_id(item.id)) is not None
        ]
        checkpoint = Checkpoint.create(
            task_id=task.id,
            version_id=task.target_version_id,
            changed_files=changed_files,
            command_run_ids=tuple(approval.command_run_id for approval in approvals),
            preview_artifact_id=None,
        )
        self._state.save_checkpoint(checkpoint)
        task.transition_to(TaskStatus.READY)
        self._state.save_task(task)
        _ = diff, commit
        return checkpoint

    def discard_task_version(self, task_id: UUID) -> Task:
        task = self.get_task(task_id)
        if task.project_id is None or task.target_version_id is None:
            raise ValueError("scratch tasks do not have a project Version")
        project = self.get_project(task.project_id)
        if project.active_version_id == task.target_version_id:
            raise InvalidTransitionError("the Active Version cannot be discarded")
        context = self._context_for(task)
        self._execute_command(
            tool_name="workspace.discard",
            scope=context.scope,
            payload={
                "project_id": str(task.project_id),
                "version_id": str(task.target_version_id),
            },
            idempotency_key=f"task:{task.id}:discard",
            operation=lambda: self._workspaces.discard_version(
                project_id=task.project_id,
                version_id=task.target_version_id,
            ),
            output=lambda _value: {"discarded": True},
        )
        version = self.get_version(task.target_version_id)
        version.visibility = VersionVisibility.REJECTED
        task.transition_to(TaskStatus.REJECTED)
        conversation = self._state.get_conversation(task.conversation_id)
        assert conversation is not None
        conversation.active_draft_version_id = None
        conversation.active_task_id = None
        self._state.save_version(version)
        self._state.save_task(task)
        self._state.save_conversation(conversation)
        return task

    def get_project(self, project_id: UUID) -> Project:
        project = self._state.get_project(project_id)
        if project is None:
            raise KeyError(f"project not found: {project_id}")
        return project

    def get_task(self, task_id: UUID) -> Task:
        task = self._state.get_task(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        return task

    def get_version(self, version_id: UUID) -> Version:
        version = self._state.get_version(version_id)
        if version is None:
            raise KeyError(f"version not found: {version_id}")
        return version

    def transition_task(self, task_id: UUID, status: TaskStatus) -> Task:
        task = self._state.get_task(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        task.transition_to(status)
        self._state.save_task(task)
        return task

    def accept_task_version(
        self,
        *,
        task_id: UUID,
        expected_project_revision: int,
        user_confirmed: bool,
    ) -> Project:
        if not user_confirmed:
            raise ApprovalRequiredError("Active Version promotion requires explicit confirmation")
        task = self._state.get_task(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        if task.status is not TaskStatus.READY:
            raise InvalidTransitionError("Task must be ready before accepting its Version")
        if task.project_id is None or task.target_version_id is None:
            raise ValueError("scratch tasks do not have promotable versions")
        context = self._context_for(task)
        dispatch = self._commands.submit(
            CommandRequest(
                tool_name="project.accept_version",
                actor="user",
                scope=context.scope,
                payload={
                    "version_id": str(task.target_version_id),
                    "expected_project_revision": expected_project_revision,
                },
                idempotency_key=(f"task:{task.id}:accept:revision:{expected_project_revision}"),
            ),
            profile=PermissionProfile.STANDARD,
            capability_overrides={},
            sandbox_healthy=False,
        )
        if not dispatch.accepted or not dispatch.requires_approval or dispatch.run is None:
            raise RuntimeError(dispatch.error_code or "Version promotion command was rejected")
        run = self._commands.decide_approval(dispatch.run.id, approved=True)
        self._commands.start(run.id)
        try:
            project = self._state.accept_version(
                project_id=task.project_id,
                version_id=task.target_version_id,
                expected_revision=expected_project_revision,
            )
            version = self.get_version(task.target_version_id)
            version.visibility = VersionVisibility.PROJECT_ACTIVE
            self._state.save_version(version)
            task.transition_to(TaskStatus.ACCEPTED)
            self._state.save_task(task)
            conversation = self._state.get_conversation(task.conversation_id)
            assert conversation is not None
            conversation.base_version_id = task.target_version_id
            conversation.active_draft_version_id = None
            conversation.active_task_id = None
            self._state.save_conversation(conversation)
        except Exception as error:
            self._commands.fail(
                run.id,
                error_code=str(getattr(error, "code", "WORKER_INTERRUPTED")),
            )
            raise
        self._commands.complete(
            run.id,
            output={"project_revision": project.revision, "version_id": str(version.id)},
        )
        return project

    def _context_for(self, task: Task) -> TaskContext:
        conversation = self._state.get_conversation(task.conversation_id)
        if conversation is None:
            raise KeyError(f"conversation not found: {task.conversation_id}")
        target = self._state.get_version(task.target_version_id) if task.target_version_id else None
        return self._build_context(task, conversation, target)

    def _build_context(
        self,
        task: Task,
        conversation: Conversation,
        target_version: Version | None,
    ) -> TaskContext:
        if target_version is not None:
            root = target_version.project_root
            read_scope = ("project_canonical", "current_conversation", "current_version")
            network_policy = "project_safe"
        else:
            root = self._workspaces.scratch_path(conversation.id, task.id).resolve(strict=False)
            read_scope = ("current_conversation",)
            network_policy = "open_web_safe"
        scope = ScopeContract.create(
            workspace_type=conversation.workspace_type,
            project_id=task.project_id,
            conversation_id=conversation.id,
            task_id=task.id,
            operation_mode=task.operation_mode,
            base_version_id=task.base_version_id,
            target_version_id=task.target_version_id,
            project_root=root,
            allowed_write_paths=(root,),
            forbidden_write_paths=(),
            execution_target=task.execution_target,
            network_policy=network_policy,
            memory_read_scope=read_scope,
            memory_write_scope=("current_conversation_draft",),
        )
        return TaskContext(task=task, target_version=target_version, scope=scope)

    def _execute_command(
        self,
        *,
        tool_name: str,
        scope: ScopeContract,
        payload: dict[str, object],
        idempotency_key: str,
        operation: Callable[[], T],
        output: Callable[[T], dict[str, object]],
    ) -> T:
        dispatch = self._commands.submit(
            CommandRequest(
                tool_name=tool_name,
                actor="core",
                scope=scope,
                payload=payload,
                idempotency_key=idempotency_key,
            ),
            profile=PermissionProfile.STANDARD,
            capability_overrides={},
            sandbox_healthy=False,
        )
        if not dispatch.accepted or dispatch.run is None:
            raise RuntimeError(dispatch.error_code or "command was rejected")
        if dispatch.requires_approval:
            raise ApprovalRequiredError(dispatch.reason or "command requires approval")
        run = dispatch.run
        if run.status is not CommandStatus.QUEUED:
            raise RuntimeError(f"command cannot execute from {run.status}")
        self._commands.start(run.id)
        try:
            result = operation()
        except Exception as error:
            self._commands.fail(
                run.id,
                error_code=str(getattr(error, "error_code", "WORKER_INTERRUPTED")),
            )
            raise
        self._commands.complete(run.id, output=output(result))
        return result
