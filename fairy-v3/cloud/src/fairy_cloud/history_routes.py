from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fairy_core.contracts.history import (
    ConversationDeleteInput,
    ConversationMoveResultModel,
    ConversationMoveToProjectInput,
    ConversationUpdateInput,
    ProjectArchivedListInput,
    ProjectArchivedPageModel,
    ProjectArchiveInput,
    ProjectDeleteInput,
    ProjectMetadataUpdateInput,
    TaskArchiveInput,
    TaskMetadataUpdateInput,
    TrashItemActionInput,
    TrashItemPageModel,
    TrashItemType,
    TrashListInput,
    TrashMutationResultModel,
    TrashPurgeAllInput,
    TrashPurgeResultModel,
)
from fairy_core.contracts.models import ConversationModel, ProjectModel, TaskModel
from fastapi import APIRouter, HTTPException, Query

Invoker = Callable[[str, dict[str, Any]], dict[str, Any]]


def install_history_routes(router: APIRouter, invoke: Invoker) -> None:
    @router.put(
        "/projects/{project_id}/metadata",
        operation_id="projects.update_metadata",
        response_model=ProjectModel,
    )
    def update_project_metadata(
        project_id: UUID,
        body: ProjectMetadataUpdateInput,
    ) -> dict[str, Any]:
        _require_match(body.project_id, project_id, "project")
        return invoke("projects.update_metadata", body.model_dump(mode="json"))

    @router.post(
        "/projects/{project_id}/archive",
        operation_id="projects.archive",
        response_model=ProjectModel,
    )
    def archive_project(project_id: UUID, body: ProjectArchiveInput) -> dict[str, Any]:
        _require_match(body.project_id, project_id, "project")
        return invoke("projects.archive", body.model_dump(mode="json"))

    @router.delete(
        "/projects/{project_id}",
        operation_id="projects.delete",
        response_model=ProjectModel,
    )
    def delete_project(project_id: UUID, body: ProjectDeleteInput) -> dict[str, Any]:
        _require_match(body.project_id, project_id, "project")
        return invoke("projects.delete", body.model_dump(mode="json"))

    @router.get(
        "/history/archived-projects",
        operation_id="projects.archived.list",
        response_model=ProjectArchivedPageModel,
    )
    def list_archived_projects(
        request: Annotated[ProjectArchivedListInput, Query()],
    ) -> dict[str, Any]:
        return invoke(
            "projects.archived.list",
            request.model_dump(mode="json", exclude_none=True),
        )

    @router.post(
        "/history/archived-projects/{project_id}/restore",
        operation_id="projects.archived.restore",
        response_model=ProjectModel,
    )
    def restore_archived_project(
        project_id: UUID,
        body: ProjectArchiveInput,
    ) -> dict[str, Any]:
        _require_match(body.project_id, project_id, "project")
        return invoke("projects.archived.restore", body.model_dump(mode="json"))

    @router.delete(
        "/history/archived-projects/{project_id}",
        operation_id="projects.archived.delete",
        response_model=ProjectModel,
    )
    def delete_archived_project(
        project_id: UUID,
        body: ProjectDeleteInput,
    ) -> dict[str, Any]:
        _require_match(body.project_id, project_id, "project")
        return invoke("projects.archived.delete", body.model_dump(mode="json"))

    @router.get(
        "/history/trash",
        operation_id="trash.items.list",
        response_model=TrashItemPageModel,
    )
    def list_trash(request: Annotated[TrashListInput, Query()]) -> dict[str, Any]:
        return invoke(
            "trash.items.list",
            request.model_dump(mode="json", exclude_none=True),
        )

    @router.post(
        "/history/trash/{item_type}/{item_id}/restore",
        operation_id="trash.items.restore",
        response_model=TrashMutationResultModel,
    )
    def restore_trash_item(
        item_type: TrashItemType,
        item_id: UUID,
        body: TrashItemActionInput,
    ) -> dict[str, Any]:
        _require_trash_match(body, item_type=item_type, item_id=item_id)
        return invoke("trash.items.restore", body.model_dump(mode="json"))

    @router.delete(
        "/history/trash/{item_type}/{item_id}",
        operation_id="trash.items.purge",
        response_model=TrashMutationResultModel,
    )
    def purge_trash_item(
        item_type: TrashItemType,
        item_id: UUID,
        body: TrashItemActionInput,
    ) -> dict[str, Any]:
        _require_trash_match(body, item_type=item_type, item_id=item_id)
        return invoke("trash.items.purge", body.model_dump(mode="json"))

    @router.delete(
        "/history/trash",
        operation_id="trash.items.purge_all",
        response_model=TrashPurgeResultModel,
    )
    def purge_all_trash(body: TrashPurgeAllInput) -> dict[str, Any]:
        return invoke("trash.items.purge_all", body.model_dump(mode="json"))

    @router.put(
        "/conversations/{conversation_id}",
        operation_id="conversations.update",
        response_model=ConversationModel,
    )
    def update_conversation(
        conversation_id: UUID,
        body: ConversationUpdateInput,
    ) -> dict[str, Any]:
        _require_match(body.conversation_id, conversation_id, "conversation")
        return invoke("conversations.update", body.model_dump(mode="json"))

    @router.delete(
        "/conversations/{conversation_id}",
        operation_id="conversations.delete",
        response_model=ConversationModel,
    )
    def delete_conversation(
        conversation_id: UUID,
        body: ConversationDeleteInput,
    ) -> dict[str, Any]:
        _require_match(body.conversation_id, conversation_id, "conversation")
        return invoke("conversations.delete", body.model_dump(mode="json"))

    @router.post(
        "/conversations/{conversation_id}/move-to-project",
        operation_id="conversations.move_to_project",
        response_model=ConversationMoveResultModel,
    )
    def move_conversation_to_project(
        conversation_id: UUID,
        body: ConversationMoveToProjectInput,
    ) -> dict[str, Any]:
        _require_match(body.conversation_id, conversation_id, "conversation")
        return invoke("conversations.move_to_project", body.model_dump(mode="json"))

    @router.put(
        "/tasks/{task_id}/metadata",
        operation_id="tasks.update_metadata",
        response_model=TaskModel,
    )
    def update_task_metadata(task_id: UUID, body: TaskMetadataUpdateInput) -> dict[str, Any]:
        _require_match(body.task_id, task_id, "task")
        return invoke("tasks.update_metadata", body.model_dump(mode="json"))

    @router.post(
        "/tasks/{task_id}/archive",
        operation_id="tasks.archive",
        response_model=TaskModel,
    )
    def archive_task(task_id: UUID, body: TaskArchiveInput) -> dict[str, Any]:
        _require_match(body.task_id, task_id, "task")
        return invoke("tasks.archive", body.model_dump(mode="json"))


def _require_match(body_id: UUID, path_id: UUID, kind: str) -> None:
    if body_id != path_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "SCOPE_MISMATCH", "message": f"{kind} id mismatch"},
        )


def _require_trash_match(
    body: TrashItemActionInput,
    *,
    item_type: TrashItemType,
    item_id: UUID,
) -> None:
    _require_match(body.item_id, item_id, "trash item")
    if body.item_type is not item_type:
        raise HTTPException(
            status_code=409,
            detail={"code": "SCOPE_MISMATCH", "message": "trash item type mismatch"},
        )


__all__ = ["install_history_routes"]
