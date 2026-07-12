from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fairy_core.contracts.history import (
    ConversationDeleteInput,
    ConversationMoveResultModel,
    ConversationMoveToProjectInput,
    ConversationUpdateInput,
    TaskArchiveInput,
    TaskMetadataUpdateInput,
)
from fairy_core.contracts.models import ConversationModel, TaskModel
from fastapi import APIRouter, HTTPException

Invoker = Callable[[str, dict[str, Any]], dict[str, Any]]


def install_history_routes(router: APIRouter, invoke: Invoker) -> None:
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


__all__ = ["install_history_routes"]
