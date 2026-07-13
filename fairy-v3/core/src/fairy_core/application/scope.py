from __future__ import annotations

from fairy_core.domain.models import Conversation, ScopeContract, Task, Version, WorkspaceType
from fairy_core.workspace.ports import WorkspaceProvisioner


def build_task_scope(
    *,
    task: Task,
    conversation: Conversation,
    target_version: Version | None,
    workspaces: WorkspaceProvisioner,
) -> ScopeContract:
    if target_version is not None:
        root = target_version.project_root
        read_scope = ("current_conversation", "user_profile", "task_episode", "current_version")
    else:
        root = workspaces.scratch_path(conversation.id, task.id).resolve(strict=False)
        read_scope = ("current_conversation", "user_profile", "task_episode")
    if conversation.workspace_type is WorkspaceType.PROJECT_CHAT:
        read_scope = ("project_canonical", *read_scope)
        network_policy = "project_safe"
    else:
        network_policy = "open_web_safe"
    return ScopeContract.create(
        workspace_type=conversation.workspace_type,
        workspace_id=task.workspace_id,
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
        memory_snapshot_id=task.memory_snapshot_id,
        memory_snapshot_hash=task.memory_snapshot_hash,
    )


__all__ = ["build_task_scope"]
