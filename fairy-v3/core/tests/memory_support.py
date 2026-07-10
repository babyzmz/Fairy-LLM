from __future__ import annotations

from pathlib import Path

from fairy_core.domain.models import (
    Conversation,
    OperationMode,
    Project,
    ProjectResidency,
    ScopeContract,
    Task,
    Version,
    VersionVisibility,
    WorkspaceType,
)


def build_memory_domain_context(tmp_path: Path, name: str = "memory"):
    project = Project.create(name=name, residency=ProjectResidency.LOCAL_ONLY)
    base = Version.create(
        project_id=project.id,
        source_conversation_id=None,
        source_task_id=None,
        parent_version_id=None,
        project_root=tmp_path / name / "base",
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
        user_request="Remember the project framework",
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=base.id,
        execution_target="local",
    )
    draft = Version.create(
        project_id=project.id,
        source_conversation_id=conversation.id,
        source_task_id=task.id,
        parent_version_id=base.id,
        project_root=tmp_path / name / "draft",
        visibility=VersionVisibility.CHAT_DRAFT,
    )
    task.bind_target_version(draft.id)
    scope = ScopeContract.create(
        workspace_type=WorkspaceType.PROJECT_CHAT,
        project_id=project.id,
        conversation_id=conversation.id,
        task_id=task.id,
        operation_mode=task.operation_mode,
        base_version_id=base.id,
        target_version_id=draft.id,
        project_root=draft.project_root,
        allowed_write_paths=(draft.project_root,),
        forbidden_write_paths=(),
        execution_target="local",
        network_policy="off",
        memory_read_scope=("project_canonical", "conversation_draft"),
        memory_write_scope=("project_canonical", "conversation_draft"),
    )
    return project, base, conversation, task, draft, scope


__all__ = ["build_memory_domain_context"]
