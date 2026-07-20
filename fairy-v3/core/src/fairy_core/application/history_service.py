from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel

from fairy_core.application.core import CoreApplication
from fairy_core.contracts.history import (
    ConversationDeleteInput,
    ConversationMoveToProjectInput,
    ConversationUpdateInput,
    ProjectArchivedListInput,
    ProjectArchiveInput,
    ProjectDeleteInput,
    ProjectMetadataUpdateInput,
    TaskArchiveInput,
    TaskMetadataUpdateInput,
    TrashItemActionInput,
    TrashItemType,
    TrashListInput,
    TrashPurgeAllInput,
)
from fairy_core.contracts.models import (
    ConversationCreate,
    ConversationIdInput,
    ConversationListInput,
    ProjectCreate,
    ProjectIdInput,
    ProjectImport,
    ProjectListInput,
    TaskListInput,
)
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory


class HistoryServiceHandlers:
    def __init__(
        self,
        *,
        application: CoreApplication,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        cancel_project_activity: Callable[[UUID], None],
    ) -> None:
        self._application = application
        self._unit_of_work_factory = unit_of_work_factory
        self._cancel_project_activity = cancel_project_activity

    @property
    def handlers(self) -> Mapping[str, Callable[[BaseModel], Any]]:
        return {
            "conversations.create": self.create_conversation,
            "conversations.delete": self.delete_conversation,
            "conversations.get": self.get_conversation,
            "conversations.list": self.list_conversations,
            "conversations.move_to_project": self.move_conversation_to_project,
            "conversations.update": self.update_conversation,
            "projects.create": self.create_project,
            "projects.archive": self.archive_project,
            "projects.archived.delete": self.delete_archived_project,
            "projects.archived.list": self.list_archived_projects,
            "projects.archived.restore": self.restore_archived_project,
            "projects.delete": self.delete_project,
            "projects.get": self.get_project,
            "projects.import": self.import_project,
            "projects.list": self.list_projects,
            "projects.update_metadata": self.update_project_metadata,
            "tasks.archive": self.archive_task,
            "tasks.list": self.list_tasks,
            "tasks.update_metadata": self.update_task_metadata,
            "trash.items.list": self.list_trash_items,
            "trash.items.purge": self.purge_trash_item,
            "trash.items.purge_all": self.purge_all_trash_items,
            "trash.items.restore": self.restore_trash_item,
        }

    def create_project(self, request: BaseModel) -> Any:
        validated = cast(ProjectCreate, request)
        return self._application.create_project(
            name=validated.name,
            residency=validated.residency,
        )

    def import_project(self, request: BaseModel) -> Any:
        validated = cast(ProjectImport, request)
        return self._application.create_project(
            name=validated.name,
            residency=validated.residency,
            source=validated.source_path,
        )

    def get_project(self, request: BaseModel) -> Any:
        return self._application.get_project(cast(ProjectIdInput, request).project_id)

    def list_projects(self, request: BaseModel) -> Any:
        validated = cast(ProjectListInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.state.list_projects(
                limit=validated.limit,
                cursor=validated.cursor,
            )

    def update_project_metadata(self, request: BaseModel) -> Any:
        validated = cast(ProjectMetadataUpdateInput, request)
        return self._application.history.update_project(
            project_id=validated.project_id,
            name=validated.name,
            pinned=validated.pinned,
            expected_revision=validated.expected_revision,
        )

    def archive_project(self, request: BaseModel) -> Any:
        validated = cast(ProjectArchiveInput, request)
        return self._application.history.archive_project(
            project_id=validated.project_id,
            expected_revision=validated.expected_revision,
        )

    def list_archived_projects(self, request: BaseModel) -> Any:
        validated = cast(ProjectArchivedListInput, request)
        return self._application.history.list_archived_projects(
            limit=validated.limit,
            cursor=validated.cursor,
        )

    def restore_archived_project(self, request: BaseModel) -> Any:
        validated = cast(ProjectArchiveInput, request)
        return self._application.history.restore_archived_project(
            project_id=validated.project_id,
            expected_revision=validated.expected_revision,
        )

    def delete_archived_project(self, request: BaseModel) -> Any:
        validated = cast(ProjectDeleteInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            project = unit_of_work.state.get_project(validated.project_id)
        if project is None:
            raise KeyError(f"project not found: {validated.project_id}")
        replayed_delete = (
            project.deleted_at is not None
            and project.purged_at is None
            and project.metadata_revision == validated.expected_revision + 1
        )
        if project.archived_at is None or (project.deleted_at is not None and not replayed_delete):
            raise InvalidTransitionError("Only an archived Project can use this operation")
        return self.delete_project(request)

    def delete_project(self, request: BaseModel) -> Any:
        validated = cast(ProjectDeleteInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            project = unit_of_work.state.get_project(validated.project_id)
        replayed_delete = (
            project is not None
            and project.deleted_at is not None
            and project.purged_at is None
            and project.metadata_revision == validated.expected_revision + 1
        )
        if validated.cancel_active and not replayed_delete:
            self._cancel_project_activity(validated.project_id)
        return self._application.history.delete_project(
            project_id=validated.project_id,
            expected_revision=validated.expected_revision,
            user_confirmed=validated.user_confirmed,
        )

    def create_conversation(self, request: BaseModel) -> Any:
        validated = cast(ConversationCreate, request)
        return self._application.create_conversation(
            project_id=validated.project_id,
            workspace_type=validated.workspace_type,
        )

    def get_conversation(self, request: BaseModel) -> Any:
        conversation_id = cast(ConversationIdInput, request).conversation_id
        with self._unit_of_work_factory() as unit_of_work:
            conversation = unit_of_work.state.get_conversation(conversation_id)
        if conversation is None:
            raise KeyError(f"conversation not found: {conversation_id}")
        return conversation

    def update_conversation(self, request: BaseModel) -> Any:
        validated = cast(ConversationUpdateInput, request)
        return self._application.history.update_conversation(
            conversation_id=validated.conversation_id,
            title=validated.title,
            pinned=validated.pinned,
            expected_revision=validated.expected_revision,
        )

    def delete_conversation(self, request: BaseModel) -> Any:
        validated = cast(ConversationDeleteInput, request)
        return self._application.history.delete_conversation(
            conversation_id=validated.conversation_id,
            expected_revision=validated.expected_revision,
            user_confirmed=validated.user_confirmed,
        )

    def list_conversations(self, request: BaseModel) -> Any:
        validated = cast(ConversationListInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.state.list_conversations(
                project_id=validated.project_id,
                limit=validated.limit,
                cursor=validated.cursor,
            )

    def move_conversation_to_project(self, request: BaseModel) -> Any:
        validated = cast(ConversationMoveToProjectInput, request)
        return self._application.history.move_to_project(
            conversation_id=validated.conversation_id,
            target_project_id=validated.target_project_id,
            expected_revision=validated.expected_revision,
            user_confirmed=validated.user_confirmed,
            idempotency_key=validated.idempotency_key,
        )

    def list_trash_items(self, request: BaseModel) -> Any:
        validated = cast(TrashListInput, request)
        return self._application.history.list_trash(
            limit=validated.limit,
            cursor=validated.cursor,
        )

    def restore_trash_item(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(TrashItemActionInput, request)
        if validated.item_type is TrashItemType.PROJECT:
            self._application.history.restore_deleted_project(
                project_id=validated.item_id,
                expected_revision=validated.expected_revision,
            )
        else:
            self._application.history.restore_deleted_conversation(
                conversation_id=validated.item_id,
                expected_revision=validated.expected_revision,
            )
        return {
            "item_type": validated.item_type,
            "item_id": validated.item_id,
            "status": "restored",
            "released_bytes": 0,
        }

    def purge_trash_item(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(TrashItemActionInput, request)
        if validated.item_type is TrashItemType.PROJECT:
            _item, released_bytes = self._application.history.purge_deleted_project(
                project_id=validated.item_id,
                expected_revision=validated.expected_revision,
                user_confirmed=validated.user_confirmed,
            )
        else:
            _item, released_bytes = self._application.history.purge_deleted_conversation(
                conversation_id=validated.item_id,
                expected_revision=validated.expected_revision,
                user_confirmed=validated.user_confirmed,
            )
        return {
            "item_type": validated.item_type,
            "item_id": validated.item_id,
            "status": "purged",
            "released_bytes": released_bytes,
        }

    def purge_all_trash_items(self, request: BaseModel) -> dict[str, int]:
        validated = cast(TrashPurgeAllInput, request)
        purged_count, released_bytes = self._application.history.purge_all_deleted(
            user_confirmed=validated.user_confirmed,
            deleted_before=validated.deleted_before,
            maintenance=validated.maintenance,
        )
        return {"purged_count": purged_count, "released_bytes": released_bytes}

    def archive_task(self, request: BaseModel) -> Any:
        validated = cast(TaskArchiveInput, request)
        return self._application.history.archive_task(
            task_id=validated.task_id,
            expected_revision=validated.expected_revision,
        )

    def list_tasks(self, request: BaseModel) -> Any:
        validated = cast(TaskListInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.state.list_tasks(
                project_id=validated.project_id,
                conversation_id=validated.conversation_id,
                limit=validated.limit,
                cursor=validated.cursor,
            )

    def update_task_metadata(self, request: BaseModel) -> Any:
        validated = cast(TaskMetadataUpdateInput, request)
        return self._application.history.update_task(
            task_id=validated.task_id,
            display_title=validated.display_title,
            pinned=validated.pinned,
            expected_revision=validated.expected_revision,
        )


def history_service_handlers(
    *,
    application: CoreApplication,
    unit_of_work_factory: CoreUnitOfWorkFactory,
    cancel_project_activity: Callable[[UUID], None],
) -> Mapping[str, Callable[[BaseModel], Any]]:
    return HistoryServiceHandlers(
        application=application,
        unit_of_work_factory=unit_of_work_factory,
        cancel_project_activity=cancel_project_activity,
    ).handlers


__all__ = ["HistoryServiceHandlers", "history_service_handlers"]
