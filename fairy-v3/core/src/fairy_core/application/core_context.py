from __future__ import annotations

from uuid import UUID

from fairy_core.application.contexts import TaskContext
from fairy_core.application.errors import ApprovalRequiredError, command_rejected
from fairy_core.application.scope import build_task_scope
from fairy_core.commanding.bus import CommandRequest
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.execution import PreviewStatus
from fairy_core.domain.models import (
    Conversation,
    Project,
    ScopeContract,
    Task,
    TaskStatus,
    Version,
    VersionVisibility,
)
from fairy_core.storage import StateStore


class CoreContextMixin:
    def get_project(self, project_id: UUID) -> Project:
        with self._transaction() as (unit_of_work, _commands):
            return self._require_project(unit_of_work.state, project_id)

    def get_task(self, task_id: UUID) -> Task:
        with self._transaction() as (unit_of_work, _commands):
            return self._require_task(unit_of_work.state, task_id)

    def get_version(self, version_id: UUID) -> Version:
        with self._transaction() as (unit_of_work, _commands):
            return self._require_version(unit_of_work.state, version_id)

    def scope_for_task(self, state: StateStore, task: Task) -> ScopeContract:
        """Resolve a Task Scope from state already bound to the caller transaction."""

        return self._context_for(state, task).scope

    def transition_task(self, task_id: UUID, status: TaskStatus) -> Task:
        with self._transaction() as (unit_of_work, _commands):
            task = self._require_task(unit_of_work.state, task_id)
            task.transition_to(status)
            unit_of_work.state.save_task(task)
            unit_of_work.commit()
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

        with self._transaction() as (unit_of_work, commands):
            task = self._require_task(unit_of_work.state, task_id)
            if task.status is not TaskStatus.READY:
                raise InvalidTransitionError("Task must be ready before accepting its Version")
            if task.project_id is None or task.target_version_id is None:
                raise ValueError("scratch tasks do not have promotable versions")
            context = self._context_for(unit_of_work.state, task)
            policy = self._execution_policy.resolve(
                unit_of_work.execution_settings,
                execution_target=context.scope.execution_target,
            )
            dispatch = commands.submit(
                CommandRequest(
                    tool_name="project.accept_version",
                    actor="user",
                    scope=context.scope,
                    payload={
                        "version_id": str(task.target_version_id),
                        "expected_project_revision": expected_project_revision,
                    },
                    idempotency_key=f"task:{task.id}:accept:revision:{expected_project_revision}",
                ),
                profile=policy.profile,
                capability_overrides=dict(policy.capability_overrides),
                sandbox_healthy=policy.sandbox_healthy,
            )
            if not dispatch.accepted or not dispatch.requires_approval or dispatch.run is None:
                raise command_rejected(dispatch, "Version promotion command was rejected")
            queued = commands.decide_approval(dispatch.run.id, approved=True)
            running = commands.start(queued.id)
            unit_of_work.commit()

        try:
            with self._transaction() as (unit_of_work, commands):
                persisted_task = self._require_task(unit_of_work.state, task.id)
                if persisted_task.status is not TaskStatus.READY:
                    raise InvalidTransitionError(
                        "Task must remain ready while accepting its Version"
                    )
                version = self._require_version(
                    unit_of_work.state,
                    persisted_task.target_version_id,
                )
                if version.visibility not in {
                    VersionVisibility.CHAT_DRAFT,
                    VersionVisibility.PROJECT_CANDIDATE,
                }:
                    raise InvalidTransitionError("Version is no longer eligible for promotion")
                project = unit_of_work.state.accept_version(
                    project_id=persisted_task.project_id,
                    version_id=persisted_task.target_version_id,
                    expected_revision=expected_project_revision,
                )
                workspace = unit_of_work.state.get_workspace(persisted_task.workspace_id)
                if workspace is None:
                    raise RuntimeError("Task Workspace was not persisted")
                workspace.accept_version(
                    persisted_task.target_version_id,
                    expected_revision=expected_project_revision,
                )
                conversation = self._require_conversation(
                    unit_of_work.state,
                    persisted_task.conversation_id,
                )
                preview = next(
                    (
                        item
                        for item in reversed(
                            unit_of_work.state.previews_for_conversation(conversation.id)
                        )
                        if item.task_id == persisted_task.id
                        and item.version_id == version.id
                        and item.status is PreviewStatus.READY
                    ),
                    None,
                )
                version.visibility = VersionVisibility.PROJECT_ACTIVE
                persisted_task.transition_to(TaskStatus.ACCEPTED)
                conversation.base_version_id = persisted_task.target_version_id
                conversation.active_draft_version_id = None
                conversation.active_task_id = None
                if preview is not None:
                    preview_revision = preview.revision
                    preview.promote_to_project_active()
                    unit_of_work.state.save_preview(preview, expected_revision=preview_revision)
                    project.active_preview_id = preview.id
                    conversation.active_preview_id = preview.id
                    unit_of_work.state.save_project(project)
                unit_of_work.state.save_version(version)
                unit_of_work.state.save_workspace(workspace)
                unit_of_work.state.save_task(persisted_task)
                unit_of_work.state.save_conversation(conversation)
                commands.complete(
                    running.id,
                    output={
                        "project_revision": project.revision,
                        "version_id": str(version.id),
                        "preview_id": str(preview.id) if preview is not None else None,
                    },
                    lease_owner=running.lease_owner,
                    lease_fence=running.lease_fence,
                )
                unit_of_work.commit()
        except Exception as error:
            with self._transaction() as (unit_of_work, commands):
                commands.fail(
                    running.id,
                    error_code=str(getattr(error, "code", "WORKER_INTERRUPTED")),
                    lease_owner=running.lease_owner,
                    lease_fence=running.lease_fence,
                )
                unit_of_work.commit()
            raise
        return project

    def _context_for(self, state: StateStore, task: Task) -> TaskContext:
        conversation = self._require_conversation(state, task.conversation_id)
        target = (
            self._require_version(state, task.target_version_id) if task.target_version_id else None
        )
        return self._build_context(task, conversation, target)

    def _build_context(
        self,
        task: Task,
        conversation: Conversation,
        target_version: Version | None,
    ) -> TaskContext:
        scope = build_task_scope(
            task=task,
            conversation=conversation,
            target_version=target_version,
            workspaces=self._workspaces,
        )
        return TaskContext(task=task, target_version=target_version, scope=scope)
