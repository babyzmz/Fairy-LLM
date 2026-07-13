from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fairy_core.application.replay_validation import normalize_idempotency_key
from fairy_core.assistant.models import ConversationMove, ImportedMessage, MessageVisibility
from fairy_core.domain.errors import IdempotencyConflictError, InvalidTransitionError
from fairy_core.domain.models import Conversation, Project, Task, TaskStatus, WorkspaceType
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.storage import StateStore


@dataclass(frozen=True, slots=True)
class ConversationMoveContext:
    source_conversation: Conversation
    destination_conversation: Conversation
    imported_count: int


class HistoryApplication:
    def __init__(self, unit_of_work_factory: CoreUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

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
            conversation.update_metadata(
                title=title,
                pinned=pinned,
                expected_revision=expected_revision,
            )
            unit_of_work.state.update_conversation_metadata(
                conversation,
                expected_revision=expected_revision,
            )
            unit_of_work.commit()
        return conversation

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
            if conversation.workspace_type is not WorkspaceType.CHAT_SCRATCH:
                raise InvalidTransitionError("Project Conversation cannot be deleted")
            self._require_inactive(unit_of_work.state, conversation)
            conversation.delete(expected_revision=expected_revision)
            unit_of_work.state.update_conversation_metadata(
                conversation,
                expected_revision=expected_revision,
            )
            unit_of_work.commit()
        return conversation

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
            self._require_inactive(unit_of_work.state, source)
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
    def _require_inactive(state: StateStore, conversation: Conversation) -> None:
        if conversation.active_task_id is None:
            return
        task = state.get_task(conversation.active_task_id)
        if task is not None and task.status not in {
            TaskStatus.READY,
            TaskStatus.ACCEPTED,
            TaskStatus.REJECTED,
            TaskStatus.ARCHIVED,
            TaskStatus.FAILED,
        }:
            raise InvalidTransitionError("active Conversation must be cancelled first")


__all__ = ["ConversationMoveContext", "HistoryApplication"]
