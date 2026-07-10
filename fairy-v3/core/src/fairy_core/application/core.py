from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fairy_core.application.errors import ApprovalRequiredError
from fairy_core.contracts.models import TaskCreate
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import (
    Conversation,
    Project,
    ProjectResidency,
    ScopeContract,
    Task,
    TaskStatus,
    Version,
    VersionVisibility,
    WorkspaceType,
)
from fairy_core.storage.state_store import SqliteStateStore
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner


@dataclass(frozen=True, slots=True)
class ProjectContext:
    project: Project
    initial_version: Version


@dataclass(frozen=True, slots=True)
class TaskContext:
    task: Task
    target_version: Version | None
    scope: ScopeContract


class CoreApplication:
    def __init__(
        self,
        *,
        state_store: SqliteStateStore,
        workspace_provisioner: FileSystemWorkspaceProvisioner,
    ) -> None:
        self._state = state_store
        self._workspaces = workspace_provisioner

    def create_project(
        self,
        *,
        name: str,
        residency: ProjectResidency,
    ) -> ProjectContext:
        project = Project.create(name=name, residency=residency)
        version_id = new_id()
        root = self._workspaces.create_initial_version(project.id, version_id)
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
        self._state.save_version(version)
        self._state.save_project(project)
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
            root = self._workspaces.fork_version(
                project_id=conversation.project_id,
                version_id=version_id,
                parent_root=parent.project_root,
            )
            target_version = Version.create(
                version_id=version_id,
                project_id=conversation.project_id,
                source_conversation_id=conversation.id,
                source_task_id=task.id,
                parent_version_id=parent.id,
                project_root=root,
                visibility=VersionVisibility.CHAT_DRAFT,
            )
            task.bind_target_version(target_version.id)
            conversation.active_draft_version_id = target_version.id
            self._state.save_version(target_version)
        else:
            self._workspaces.create_scratch(conversation.id, task.id)

        task.transition_to(TaskStatus.RESOLVING_SCOPE)
        conversation.active_task_id = task.id
        self._state.save_task(task, idempotency_key=request.idempotency_key)
        self._state.save_conversation(conversation)
        return self._build_context(task, conversation, target_version)

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
        project = self._state.accept_version(
            project_id=task.project_id,
            version_id=task.target_version_id,
            expected_revision=expected_project_revision,
        )
        version = self._state.get_version(task.target_version_id)
        assert version is not None
        version.visibility = VersionVisibility.PROJECT_ACTIVE
        self._state.save_version(version)
        task.transition_to(TaskStatus.ACCEPTED)
        self._state.save_task(task)
        conversation = self._state.get_conversation(task.conversation_id)
        assert conversation is not None
        conversation.base_version_id = task.target_version_id
        conversation.active_draft_version_id = None
        self._state.save_conversation(conversation)
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
