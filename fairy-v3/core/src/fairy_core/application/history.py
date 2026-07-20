from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from fairy_core.application.replay_validation import normalize_idempotency_key
from fairy_core.assistant.models import ConversationMove, ImportedMessage, MessageVisibility
from fairy_core.commanding.models import EventVisibility
from fairy_core.domain.errors import (
    IdempotencyConflictError,
    InvalidTransitionError,
    ProjectBusyError,
)
from fairy_core.domain.execution import PreviewStatus, RuntimeStatus
from fairy_core.domain.models import (
    Conversation,
    Project,
    ProjectResidency,
    Task,
    TaskStatus,
    WorkspaceType,
)
from fairy_core.persistence.unit_of_work import CoreUnitOfWork, CoreUnitOfWorkFactory
from fairy_core.storage import StateStore
from fairy_core.storage.pagination import StatePage, decode_cursor, encode_cursor, validate_limit
from fairy_core.workspace.ports import WorkspaceProvisioner


@dataclass(frozen=True, slots=True)
class ConversationMoveContext:
    source_conversation: Conversation
    destination_conversation: Conversation
    imported_count: int


@dataclass(frozen=True, slots=True)
class ArchivedProjectSummary:
    project: Project
    thread_count: int
    archived_at: datetime


@dataclass(frozen=True, slots=True)
class TrashItemRecord:
    item_type: str
    item_id: UUID
    title: str
    source_project_id: UUID | None
    source_project_title: str | None
    thread_count: int
    deleted_at: datetime
    estimated_bytes: int
    can_restore: bool
    metadata_revision: int


class TaskAttachmentStore(Protocol):
    def task_size(self, task_ids: tuple[UUID, ...]) -> int: ...

    def purge_tasks(self, task_ids: tuple[UUID, ...]) -> int: ...


class HistoryApplication:
    def __init__(
        self,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        workspace_provisioner: WorkspaceProvisioner,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._workspaces = workspace_provisioner
        self._attachments: TaskAttachmentStore | None = None

    def configure_attachment_store(self, store: TaskAttachmentStore) -> None:
        self._attachments = store

    def update_conversation(
        self,
        *,
        conversation_id: UUID,
        title: str | None,
        pinned: bool | None,
        expected_revision: int,
    ) -> Conversation:
        with self._unit_of_work_factory() as unit_of_work:
            conversation = self._require_conversation(unit_of_work.state, conversation_id)
            if self._conversation_update_replayed(
                conversation,
                title=title,
                pinned=pinned,
                expected_revision=expected_revision,
            ):
                return conversation
            conversation.update_metadata(
                title=title,
                pinned=pinned,
                expected_revision=expected_revision,
            )
            unit_of_work.state.update_conversation_metadata(
                conversation,
                expected_revision=expected_revision,
            )
            unit_of_work.commands.append_domain_event(
                event_type="conversation.updated",
                visibility=EventVisibility.USER,
                message="Conversation updated",
                payload={"revision": conversation.revision},
                actor="user",
                project_id=conversation.project_id,
                conversation_id=conversation.id,
            )
            unit_of_work.commit()
        return conversation

    def update_project(
        self,
        *,
        project_id: UUID,
        name: str | None,
        pinned: bool | None,
        expected_revision: int,
    ) -> Project:
        with self._unit_of_work_factory() as unit_of_work:
            project = self._require_project(unit_of_work.state, project_id)
            if self._project_update_replayed(
                project,
                name=name,
                pinned=pinned,
                expected_revision=expected_revision,
            ):
                return project
            project.update_metadata(
                name=name,
                pinned=pinned,
                expected_revision=expected_revision,
            )
            unit_of_work.state.update_project_metadata(
                project,
                expected_revision=expected_revision,
            )
            unit_of_work.commands.append_domain_event(
                event_type="project.updated",
                visibility=EventVisibility.USER,
                message="Project updated",
                payload={"metadata_revision": project.metadata_revision},
                actor="user",
                project_id=project.id,
            )
            unit_of_work.commit()
        return project

    def archive_project(self, *, project_id: UUID, expected_revision: int) -> Project:
        with self._unit_of_work_factory() as unit_of_work:
            project = self._require_project(unit_of_work.state, project_id)
            if (
                project.metadata_revision == expected_revision + 1
                and project.archived_at is not None
                and project.deleted_at is None
                and project.purged_at is None
            ):
                return project
            self._require_project_inactive(unit_of_work, project.id)
            project.archive(expected_revision=expected_revision)
            unit_of_work.state.update_project_metadata(
                project,
                expected_revision=expected_revision,
            )
            unit_of_work.commands.append_domain_event(
                event_type="project.archived",
                visibility=EventVisibility.USER,
                message="Project archived",
                payload={"metadata_revision": project.metadata_revision},
                actor="user",
                project_id=project.id,
            )
            unit_of_work.commit()
        return project

    def restore_archived_project(
        self,
        *,
        project_id: UUID,
        expected_revision: int,
    ) -> Project:
        with self._unit_of_work_factory() as unit_of_work:
            project = self._require_project(unit_of_work.state, project_id)
            if (
                project.metadata_revision == expected_revision + 1
                and project.archived_at is None
                and project.deleted_at is None
                and project.purged_at is None
                and unit_of_work.commands.has_domain_event(
                    event_type="project.restored",
                    project_id=project.id,
                    conversation_id=None,
                    payload={
                        "metadata_revision": project.metadata_revision,
                        "restore_source": "archive",
                    },
                )
            ):
                return project
            project.restore_archive(expected_revision=expected_revision)
            unit_of_work.state.update_project_metadata(
                project,
                expected_revision=expected_revision,
            )
            unit_of_work.commands.append_domain_event(
                event_type="project.restored",
                visibility=EventVisibility.USER,
                message="Project restored",
                payload={
                    "metadata_revision": project.metadata_revision,
                    "restore_source": "archive",
                },
                actor="user",
                project_id=project.id,
            )
            unit_of_work.commit()
        return project

    def delete_project(
        self,
        *,
        project_id: UUID,
        expected_revision: int,
        user_confirmed: bool,
    ) -> Project:
        if not user_confirmed:
            raise ValueError("Project deletion requires explicit confirmation")
        with self._unit_of_work_factory() as unit_of_work:
            project = self._require_project(unit_of_work.state, project_id)
            if (
                project.metadata_revision == expected_revision + 1
                and project.deleted_at is not None
                and project.purged_at is None
            ):
                return project
            self._require_project_inactive(unit_of_work, project.id)
            conversations = self._project_conversations(unit_of_work.state, project.id)
            original_project_revision = project.metadata_revision
            project.delete(expected_revision=expected_revision)
            unit_of_work.state.update_project_metadata(
                project,
                expected_revision=original_project_revision,
            )
            for conversation in conversations:
                if conversation.deleted_at is not None:
                    continue
                original_conversation_revision = conversation.revision
                conversation.delete(
                    expected_revision=original_conversation_revision,
                    by_project=True,
                )
                unit_of_work.state.update_conversation_metadata(
                    conversation,
                    expected_revision=original_conversation_revision,
                )
            unit_of_work.commands.append_domain_event(
                event_type="project.deleted",
                visibility=EventVisibility.USER,
                message="Project moved to Recently deleted",
                payload={
                    "metadata_revision": project.metadata_revision,
                    "thread_count": len(conversations),
                },
                actor="user",
                project_id=project.id,
            )
            unit_of_work.commit()
        return project

    def restore_deleted_project(
        self,
        *,
        project_id: UUID,
        expected_revision: int,
    ) -> Project:
        with self._unit_of_work_factory() as unit_of_work:
            project = self._require_project(unit_of_work.state, project_id)
            if (
                project.metadata_revision == expected_revision + 1
                and project.deleted_at is None
                and project.purged_at is None
                and unit_of_work.commands.has_domain_event(
                    event_type="project.restored",
                    project_id=project.id,
                    conversation_id=None,
                    payload={
                        "metadata_revision": project.metadata_revision,
                        "restore_source": "trash",
                    },
                )
            ):
                return project
            conversations = self._project_conversations(unit_of_work.state, project.id)
            original_project_revision = project.metadata_revision
            project.restore_deleted(expected_revision=expected_revision)
            unit_of_work.state.update_project_metadata(
                project,
                expected_revision=original_project_revision,
            )
            for conversation in conversations:
                if (
                    conversation.deleted_at is None
                    or conversation.deleted_by_project_at is None
                    or conversation.purged_at is not None
                ):
                    continue
                original_conversation_revision = conversation.revision
                conversation.restore_deleted(expected_revision=original_conversation_revision)
                unit_of_work.state.update_conversation_metadata(
                    conversation,
                    expected_revision=original_conversation_revision,
                )
            unit_of_work.commands.append_domain_event(
                event_type="project.restored",
                visibility=EventVisibility.USER,
                message="Project restored",
                payload={
                    "metadata_revision": project.metadata_revision,
                    "thread_count": len(conversations),
                    "restore_source": "trash",
                },
                actor="user",
                project_id=project.id,
            )
            unit_of_work.commit()
        return project

    def delete_conversation(
        self,
        *,
        conversation_id: UUID,
        expected_revision: int,
        user_confirmed: bool,
    ) -> Conversation:
        if not user_confirmed:
            raise ValueError("Conversation deletion requires explicit confirmation")
        with self._unit_of_work_factory() as unit_of_work:
            conversation = self._require_conversation(unit_of_work.state, conversation_id)
            if (
                conversation.revision == expected_revision + 1
                and conversation.deleted_at is not None
                and conversation.purged_at is None
            ):
                return conversation
            self._require_inactive(unit_of_work, conversation)
            conversation.delete(expected_revision=expected_revision)
            unit_of_work.state.update_conversation_metadata(
                conversation,
                expected_revision=expected_revision,
            )
            unit_of_work.commands.append_domain_event(
                event_type="conversation.deleted",
                visibility=EventVisibility.USER,
                message="Conversation moved to Recently deleted",
                payload={"revision": conversation.revision},
                actor="user",
                project_id=conversation.project_id,
                conversation_id=conversation.id,
            )
            unit_of_work.commit()
        return conversation

    def restore_deleted_conversation(
        self,
        *,
        conversation_id: UUID,
        expected_revision: int,
    ) -> Conversation:
        with self._unit_of_work_factory() as unit_of_work:
            conversation = self._require_conversation(unit_of_work.state, conversation_id)
            if (
                conversation.revision == expected_revision + 1
                and conversation.deleted_at is None
                and conversation.purged_at is None
                and unit_of_work.commands.has_domain_event(
                    event_type="conversation.restored",
                    project_id=conversation.project_id,
                    conversation_id=conversation.id,
                    payload={
                        "revision": conversation.revision,
                        "restore_source": "trash",
                    },
                )
            ):
                return conversation
            if conversation.project_id is not None:
                project = self._require_project(unit_of_work.state, conversation.project_id)
                if project.deleted_at is not None or project.purged_at is not None:
                    raise InvalidTransitionError(
                        "Project Conversation must be restored with its Project"
                    )
            original_revision = conversation.revision
            conversation.restore_deleted(expected_revision=expected_revision)
            unit_of_work.state.update_conversation_metadata(
                conversation,
                expected_revision=original_revision,
            )
            unit_of_work.commands.append_domain_event(
                event_type="conversation.restored",
                visibility=EventVisibility.USER,
                message="Conversation restored",
                payload={
                    "revision": conversation.revision,
                    "restore_source": "trash",
                },
                actor="user",
                project_id=conversation.project_id,
                conversation_id=conversation.id,
            )
            unit_of_work.commit()
        return conversation

    def list_archived_projects(
        self,
        *,
        limit: int,
        cursor: str | None,
    ) -> StatePage[ArchivedProjectSummary]:
        with self._unit_of_work_factory() as unit_of_work:
            page = unit_of_work.state.list_projects(
                limit=limit,
                cursor=cursor,
                lifecycle="archived",
            )
            items = tuple(
                ArchivedProjectSummary(
                    project=project,
                    thread_count=len(
                        self._project_conversations(
                            unit_of_work.state,
                            project.id,
                            lifecycle="active",
                        )
                    ),
                    archived_at=self._require_timestamp(
                        project.archived_at,
                        "Archived Project is missing archived_at",
                    ),
                )
                for project in page.items
            )
        return StatePage(items=items, next_cursor=page.next_cursor)

    def list_trash(
        self,
        *,
        limit: int,
        cursor: str | None,
    ) -> StatePage[TrashItemRecord]:
        validate_limit(limit)
        scope: dict[str, str | None] = {"lifecycle": "deleted"}
        position = decode_cursor(cursor, collection="trash", scope=scope)
        with self._unit_of_work_factory() as unit_of_work:
            deleted_projects = self._all_projects(unit_of_work.state, lifecycle="deleted")
            deleted_conversations = self._all_conversations(
                unit_of_work.state,
                lifecycle="deleted",
            )
            projects_by_id = {
                project.id: project
                for project in (
                    *deleted_projects,
                    *self._all_projects(unit_of_work.state, lifecycle="active"),
                    *self._all_projects(unit_of_work.state, lifecycle="archived"),
                )
            }
            items = [
                TrashItemRecord(
                    item_type="project",
                    item_id=project.id,
                    title=project.name,
                    source_project_id=None,
                    source_project_title=None,
                    thread_count=len(
                        self._project_conversations(
                            unit_of_work.state,
                            project.id,
                            lifecycle="all",
                        )
                    ),
                    deleted_at=self._require_timestamp(
                        project.deleted_at,
                        "Deleted Project is missing deleted_at",
                    ),
                    estimated_bytes=(
                        self._workspaces.workspace_size(project.workspace_id)
                        + self._attachment_size(
                            tuple(
                                task.id
                                for task in self._project_tasks(unit_of_work.state, project.id)
                            )
                        )
                    ),
                    can_restore=True,
                    metadata_revision=project.metadata_revision,
                )
                for project in deleted_projects
            ]
            for conversation in deleted_conversations:
                if conversation.deleted_by_project_at is not None:
                    continue
                project = (
                    projects_by_id.get(conversation.project_id)
                    if conversation.project_id is not None
                    else None
                )
                items.append(
                    TrashItemRecord(
                        item_type=(
                            "project_conversation"
                            if conversation.project_id is not None
                            else "conversation"
                        ),
                        item_id=conversation.id,
                        title=conversation.title,
                        source_project_id=conversation.project_id,
                        source_project_title=project.name if project is not None else None,
                        thread_count=0,
                        deleted_at=self._require_timestamp(
                            conversation.deleted_at,
                            "Deleted Conversation is missing deleted_at",
                        ),
                        estimated_bytes=(
                            self._workspaces.workspace_size(conversation.workspace_id)
                            if conversation.project_id is None
                            and conversation.workspace_id is not None
                            else 0
                        )
                        + self._attachment_size(
                            tuple(
                                task.id
                                for task in self._conversation_tasks(
                                    unit_of_work.state,
                                    conversation,
                                )
                            )
                        ),
                        can_restore=(project is None or project.deleted_at is None),
                        metadata_revision=conversation.revision,
                    )
                )
        items.sort(key=lambda item: (item.deleted_at, item.item_id.int), reverse=True)
        if position is not None:
            items = [
                item
                for item in items
                if (item.deleted_at, item.item_id.int)
                < (position.created_at, position.entity_id.int)
            ]
        selected = tuple(items[:limit])
        next_cursor = None
        if len(items) > limit and selected:
            last = selected[-1]
            next_cursor = encode_cursor(
                collection="trash",
                created_at=last.deleted_at,
                entity_id=last.item_id,
                scope=scope,
            )
        return StatePage(items=selected, next_cursor=next_cursor)

    def purge_deleted_conversation(
        self,
        *,
        conversation_id: UUID,
        expected_revision: int,
        user_confirmed: bool,
    ) -> tuple[Conversation, int]:
        if not user_confirmed:
            raise ValueError("Permanent deletion requires explicit confirmation")
        with self._unit_of_work_factory() as unit_of_work:
            conversation = self._require_conversation(unit_of_work.state, conversation_id)
            if (
                conversation.revision == expected_revision + 1
                and conversation.purged_at is not None
            ):
                return conversation, 0
            self._require_inactive(unit_of_work, conversation)
            if conversation.deleted_at is None:
                raise InvalidTransitionError("Conversation must be deleted before it is purged")
            if conversation.purged_at is not None:
                raise InvalidTransitionError("Conversation is already purged")
            task_ids = tuple(
                task.id for task in self._conversation_tasks(unit_of_work.state, conversation)
            )
            original_revision = conversation.revision
            conversation.mark_purged(expected_revision=expected_revision)
            unit_of_work.state.update_conversation_metadata(
                conversation,
                expected_revision=original_revision,
            )
            unit_of_work.assistant.purge_conversation_content(conversation.id)
            if conversation.project_id is None and conversation.workspace_id is not None:
                unit_of_work.assistant.purge_workspace_content(conversation.workspace_id)
            unit_of_work.commands.append_domain_event(
                event_type="conversation.purged",
                visibility=EventVisibility.USER,
                message="Conversation permanently deleted",
                payload={"revision": conversation.revision},
                actor="user",
                project_id=conversation.project_id,
                conversation_id=conversation.id,
            )
            released_bytes = (
                self._workspaces.purge_workspace(conversation.workspace_id)
                if conversation.project_id is None and conversation.workspace_id is not None
                else 0
            )
            released_bytes += self._purge_attachments(task_ids)
            unit_of_work.commit()
        return conversation, released_bytes

    def purge_deleted_project(
        self,
        *,
        project_id: UUID,
        expected_revision: int,
        user_confirmed: bool,
    ) -> tuple[Project, int]:
        if not user_confirmed:
            raise ValueError("Permanent deletion requires explicit confirmation")
        with self._unit_of_work_factory() as unit_of_work:
            project = self._require_project(unit_of_work.state, project_id)
            if project.metadata_revision == expected_revision + 1 and project.purged_at is not None:
                return project, 0
            self._require_project_inactive(unit_of_work, project.id)
            if project.deleted_at is None:
                raise InvalidTransitionError("Project must be deleted before it is purged")
            original_project_revision = project.metadata_revision
            project.mark_purged(expected_revision=expected_revision)
            unit_of_work.state.update_project_metadata(
                project,
                expected_revision=original_project_revision,
            )
            task_ids = tuple(
                task.id for task in self._project_tasks(unit_of_work.state, project.id)
            )
            conversations = self._project_conversations(unit_of_work.state, project.id)
            for conversation in conversations:
                if conversation.deleted_at is None or conversation.purged_at is not None:
                    continue
                original_conversation_revision = conversation.revision
                conversation.mark_purged(expected_revision=original_conversation_revision)
                unit_of_work.state.update_conversation_metadata(
                    conversation,
                    expected_revision=original_conversation_revision,
                )
                unit_of_work.assistant.purge_conversation_content(conversation.id)
            unit_of_work.assistant.purge_project_content(project.id)
            unit_of_work.assistant.purge_workspace_content(project.workspace_id)
            unit_of_work.commands.append_domain_event(
                event_type="project.purged",
                visibility=EventVisibility.USER,
                message="Project permanently deleted",
                payload={"metadata_revision": project.metadata_revision},
                actor="user",
                project_id=project.id,
            )
            released_bytes = self._workspaces.purge_workspace(project.workspace_id)
            released_bytes += self._purge_attachments(task_ids)
            unit_of_work.commit()
        return project, released_bytes

    def purge_all_deleted(
        self,
        *,
        user_confirmed: bool,
        deleted_before: datetime | None = None,
        maintenance: bool = False,
    ) -> tuple[int, int]:
        if not user_confirmed:
            raise ValueError("Permanent deletion requires explicit confirmation")
        page = self.list_trash(limit=100, cursor=None)
        items = list(page.items)
        while page.next_cursor is not None:
            page = self.list_trash(limit=100, cursor=page.next_cursor)
            items.extend(page.items)
        purged = 0
        released_bytes = 0
        for item in items:
            if deleted_before is not None and item.deleted_at > deleted_before:
                continue
            if maintenance and not self._maintenance_item_eligible(item):
                continue
            if item.item_type == "project":
                _project, released = self.purge_deleted_project(
                    project_id=item.item_id,
                    expected_revision=item.metadata_revision,
                    user_confirmed=True,
                )
            else:
                _conversation, released = self.purge_deleted_conversation(
                    conversation_id=item.item_id,
                    expected_revision=item.metadata_revision,
                    user_confirmed=True,
                )
            released_bytes += released
            purged += 1
        return purged, released_bytes

    def move_to_project(
        self,
        *,
        conversation_id: UUID,
        target_project_id: UUID,
        expected_revision: int,
        user_confirmed: bool,
        idempotency_key: str,
    ) -> ConversationMoveContext:
        if not user_confirmed:
            raise ValueError("Conversation move requires explicit confirmation")
        normalized_key = normalize_idempotency_key(idempotency_key)
        with self._unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.assistant.find_conversation_move(normalized_key)
            if existing is not None:
                if (
                    existing.source_conversation_id != conversation_id
                    or existing.target_project_id != target_project_id
                ):
                    raise IdempotencyConflictError(
                        "Conversation move idempotency key does not match the request"
                    )
                return ConversationMoveContext(
                    source_conversation=self._require_conversation(
                        unit_of_work.state,
                        conversation_id,
                    ),
                    destination_conversation=self._require_conversation(
                        unit_of_work.state,
                        existing.destination_conversation_id,
                    ),
                    imported_count=existing.imported_count,
                )

            source = self._require_conversation(unit_of_work.state, conversation_id)
            if source.workspace_type is not WorkspaceType.CHAT_SCRATCH:
                raise InvalidTransitionError("Only scratch Conversations can move to a Project")
            if source.deleted_at is not None:
                raise InvalidTransitionError("Deleted Conversation cannot be moved")
            self._require_inactive(unit_of_work, source)
            project = self._require_project(unit_of_work.state, target_project_id)
            destination = Conversation.create(
                project_id=project.id,
                workspace_id=project.workspace_id,
                workspace_type=WorkspaceType.PROJECT_CHAT,
                base_version_id=project.active_version_id,
                title=source.title,
            )
            unit_of_work.state.save_conversation(destination)
            source_messages = self._visible_messages(unit_of_work, source.id)
            for source_message in source_messages:
                unit_of_work.assistant.append_imported_message(
                    ImportedMessage.from_message(
                        destination_conversation_id=destination.id,
                        sequence=unit_of_work.assistant.next_message_sequence(destination.id),
                        source=source_message,
                    )
                )
            unit_of_work.assistant.save_conversation_move(
                ConversationMove(
                    idempotency_key=normalized_key,
                    source_conversation_id=source.id,
                    destination_conversation_id=destination.id,
                    target_project_id=project.id,
                    imported_count=len(source_messages),
                )
            )
            source.delete(expected_revision=expected_revision)
            unit_of_work.state.update_conversation_metadata(
                source,
                expected_revision=expected_revision,
            )
            unit_of_work.commit()
        return ConversationMoveContext(source, destination, len(source_messages))

    def update_task(
        self,
        *,
        task_id: UUID,
        display_title: str | None,
        pinned: bool | None,
        expected_revision: int,
    ) -> Task:
        with self._unit_of_work_factory() as unit_of_work:
            task = self._require_task(unit_of_work.state, task_id)
            task.update_metadata(
                display_title=display_title,
                pinned=pinned,
                expected_revision=expected_revision,
            )
            unit_of_work.state.update_task_metadata(task, expected_revision=expected_revision)
            unit_of_work.commit()
        return task

    def archive_task(self, *, task_id: UUID, expected_revision: int) -> Task:
        with self._unit_of_work_factory() as unit_of_work:
            task = self._require_task(unit_of_work.state, task_id)
            task.archive(expected_revision=expected_revision)
            unit_of_work.state.update_task_metadata(task, expected_revision=expected_revision)
            unit_of_work.commit()
        return task

    @staticmethod
    def _visible_messages(unit_of_work, conversation_id: UUID):
        items = []
        cursor: str | None = None
        while True:
            page = unit_of_work.assistant.list_messages(
                conversation_id=conversation_id,
                limit=100,
                cursor=cursor,
                allowed_visibilities=frozenset(
                    {MessageVisibility.USER, MessageVisibility.DEVELOPER}
                ),
            )
            items.extend(page.items)
            if page.next_cursor is None:
                return items
            cursor = page.next_cursor

    @staticmethod
    def _require_project(state: StateStore, project_id: UUID) -> Project:
        project = state.get_project(project_id)
        if project is None:
            raise KeyError(f"project not found: {project_id}")
        return project

    @staticmethod
    def _require_conversation(state: StateStore, conversation_id: UUID) -> Conversation:
        conversation = state.get_conversation(conversation_id)
        if conversation is None:
            raise KeyError(f"conversation not found: {conversation_id}")
        return conversation

    @staticmethod
    def _require_task(state: StateStore, task_id: UUID) -> Task:
        task = state.get_task(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        return task

    @staticmethod
    def _project_update_replayed(
        project: Project,
        *,
        name: str | None,
        pinned: bool | None,
        expected_revision: int,
    ) -> bool:
        if project.metadata_revision != expected_revision + 1:
            return False
        if project.deleted_at is not None or project.purged_at is not None:
            return False
        if name is not None and project.name != name.strip():
            return False
        return pinned is None or (project.pinned_at is not None) is pinned

    @staticmethod
    def _conversation_update_replayed(
        conversation: Conversation,
        *,
        title: str | None,
        pinned: bool | None,
        expected_revision: int,
    ) -> bool:
        if conversation.revision != expected_revision + 1:
            return False
        if conversation.deleted_at is not None or conversation.purged_at is not None:
            return False
        if title is not None and conversation.title != title.strip():
            return False
        return pinned is None or (conversation.pinned_at is not None) is pinned

    def _attachment_size(self, task_ids: tuple[UUID, ...]) -> int:
        return self._attachments.task_size(task_ids) if self._attachments is not None else 0

    def _purge_attachments(self, task_ids: tuple[UUID, ...]) -> int:
        return self._attachments.purge_tasks(task_ids) if self._attachments is not None else 0

    @classmethod
    def _require_inactive(
        cls,
        unit_of_work: CoreUnitOfWork,
        conversation: Conversation,
    ) -> None:
        tasks = cls._conversation_tasks(unit_of_work.state, conversation)
        if any(task.status not in _TERMINAL_TASK_STATUSES for task in tasks):
            raise InvalidTransitionError("active Conversation must be cancelled first")
        if unit_of_work.assistant.nonterminal_turns_for_tasks(tuple(task.id for task in tasks)):
            raise InvalidTransitionError("active Conversation must be cancelled first")
        for task in tasks:
            preview = unit_of_work.state.preview_for_task(task.id, include_terminal=True)
            if preview is not None and preview.status in _ACTIVE_PREVIEW_STATUSES:
                raise InvalidTransitionError("active Conversation must be cancelled first")
            if any(
                runtime.status in _ACTIVE_RUNTIME_STATUSES
                for runtime in unit_of_work.state.runtimes_for_task(task.id)
            ):
                raise InvalidTransitionError("active Conversation must be cancelled first")

    def _maintenance_item_eligible(self, item: TrashItemRecord) -> bool:
        with self._unit_of_work_factory() as unit_of_work:
            if item.item_type == "project":
                project = self._require_project(unit_of_work.state, item.item_id)
                if project.residency is not ProjectResidency.LOCAL_ONLY:
                    return False
                try:
                    self._require_project_inactive(unit_of_work, project.id)
                except ProjectBusyError:
                    return False
                return True

            conversation = self._require_conversation(unit_of_work.state, item.item_id)
            if conversation.project_id is not None:
                project = self._require_project(unit_of_work.state, conversation.project_id)
                if project.residency is not ProjectResidency.LOCAL_ONLY:
                    return False
            try:
                self._require_inactive(unit_of_work, conversation)
            except InvalidTransitionError:
                return False
            return True

    @classmethod
    def _require_project_inactive(
        cls,
        unit_of_work: CoreUnitOfWork,
        project_id: UUID,
    ) -> None:
        tasks = cls._project_tasks(unit_of_work.state, project_id)
        if any(task.status not in _TERMINAL_TASK_STATUSES for task in tasks):
            raise ProjectBusyError("Project has active work")
        if unit_of_work.assistant.nonterminal_turns_for_tasks(tuple(task.id for task in tasks)):
            raise ProjectBusyError("Project has an active Assistant turn")
        for task in tasks:
            preview = unit_of_work.state.preview_for_task(task.id, include_terminal=True)
            if preview is not None and preview.status in _ACTIVE_PREVIEW_STATUSES:
                raise ProjectBusyError("Project has an active Preview")
            if any(
                runtime.status in _ACTIVE_RUNTIME_STATUSES
                for runtime in unit_of_work.state.runtimes_for_task(task.id)
            ):
                raise ProjectBusyError("Project has an active Runtime")

    @staticmethod
    def _project_tasks(state: StateStore, project_id: UUID) -> tuple[Task, ...]:
        items: list[Task] = []
        cursor: str | None = None
        while True:
            page = state.list_tasks(
                project_id=project_id,
                conversation_id=None,
                limit=100,
                cursor=cursor,
            )
            items.extend(page.items)
            if page.next_cursor is None:
                return tuple(items)
            cursor = page.next_cursor

    @staticmethod
    def _conversation_tasks(
        state: StateStore,
        conversation: Conversation,
    ) -> tuple[Task, ...]:
        items: list[Task] = []
        cursor: str | None = None
        while True:
            page = state.list_tasks(
                project_id=conversation.project_id,
                conversation_id=conversation.id,
                limit=100,
                cursor=cursor,
            )
            items.extend(page.items)
            if page.next_cursor is None:
                return tuple(items)
            cursor = page.next_cursor

    @staticmethod
    def _project_conversations(
        state: StateStore,
        project_id: UUID,
        *,
        lifecycle: str = "all",
    ) -> tuple[Conversation, ...]:
        items: list[Conversation] = []
        cursor: str | None = None
        while True:
            page = state.list_conversations(
                project_id=project_id,
                limit=100,
                cursor=cursor,
                lifecycle=lifecycle,
            )
            items.extend(page.items)
            if page.next_cursor is None:
                return tuple(items)
            cursor = page.next_cursor

    @staticmethod
    def _all_projects(state: StateStore, *, lifecycle: str) -> tuple[Project, ...]:
        items: list[Project] = []
        cursor: str | None = None
        while True:
            page = state.list_projects(limit=100, cursor=cursor, lifecycle=lifecycle)
            items.extend(page.items)
            if page.next_cursor is None:
                return tuple(items)
            cursor = page.next_cursor

    @staticmethod
    def _all_conversations(
        state: StateStore,
        *,
        lifecycle: str,
    ) -> tuple[Conversation, ...]:
        items: list[Conversation] = []
        cursor: str | None = None
        while True:
            page = state.list_conversations(
                project_id=None,
                limit=100,
                cursor=cursor,
                lifecycle=lifecycle,
            )
            items.extend(page.items)
            if page.next_cursor is None:
                return tuple(items)
            cursor = page.next_cursor

    @staticmethod
    def _require_timestamp(value: datetime | None, message: str) -> datetime:
        if value is None:
            raise InvalidTransitionError(message)
        return value


_TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.READY,
        TaskStatus.ACCEPTED,
        TaskStatus.REJECTED,
        TaskStatus.ARCHIVED,
        TaskStatus.FAILED,
    }
)

_ACTIVE_PREVIEW_STATUSES = frozenset(
    {
        PreviewStatus.CREATED,
        PreviewStatus.STARTING,
        PreviewStatus.READY,
        PreviewStatus.STOPPING,
        PreviewStatus.INTERRUPTED,
    }
)

_ACTIVE_RUNTIME_STATUSES = frozenset(
    {
        RuntimeStatus.CREATED,
        RuntimeStatus.STARTING,
        RuntimeStatus.RUNNING,
        RuntimeStatus.STOPPING,
        RuntimeStatus.INTERRUPTED,
    }
)


__all__ = [
    "ArchivedProjectSummary",
    "ConversationMoveContext",
    "HistoryApplication",
    "TrashItemRecord",
]
