from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Table, and_, or_, select
from sqlalchemy.engine import RowMapping

from fairy_core.domain.execution import Approval
from fairy_core.domain.models import Conversation, Project, Task, Version
from fairy_core.storage.pagination import StatePage, decode_cursor, encode_cursor, validate_limit
from fairy_core.storage.schema import approvals, conversations, projects, tasks, versions


class CollectionStateStoreMixin:
    def get_conversations_by_ids(self, ids: tuple[UUID, ...]) -> tuple[Conversation, ...]:
        return tuple(
            self._conversation_from_row(row) for row in self._rows_by_ids(conversations, ids)
        )

    def get_tasks_by_ids(self, ids: tuple[UUID, ...]) -> tuple[Task, ...]:
        return tuple(self._task_from_row(row) for row in self._rows_by_ids(tasks, ids))

    def _rows_by_ids(self, table: Table, ids: tuple[UUID, ...]) -> tuple[RowMapping, ...]:
        if len(ids) > 500:
            raise ValueError("State projection batch exceeds 500 IDs")
        if not ids:
            return ()
        with self._session.read() as connection:
            return tuple(connection.execute(select(table).where(
                table.c.tenant_id == self._tenant_id, table.c.id.in_(tuple(map(str, ids))),
            )).mappings())

    def list_projects(
        self,
        *,
        limit: int,
        cursor: str | None,
        lifecycle: str = "active",
    ) -> StatePage[Project]:
        lifecycle_filters = {
            "active": (projects.c.archived_at.is_(None), projects.c.deleted_at.is_(None)),
            "archived": (
                projects.c.archived_at.is_not(None),
                projects.c.deleted_at.is_(None),
            ),
            "deleted": (
                projects.c.deleted_at.is_not(None),
                projects.c.purged_at.is_(None),
            ),
        }
        try:
            filters = lifecycle_filters[lifecycle]
        except KeyError as error:
            raise ValueError("unsupported Project lifecycle filter") from error
        rows, next_cursor = self._page_rows(
            projects,
            collection="projects",
            scope={"lifecycle": lifecycle},
            filters=filters,
            limit=limit,
            cursor=cursor,
        )
        return StatePage(
            items=tuple(self._project_from_row(row) for row in rows),
            next_cursor=next_cursor,
        )

    def list_conversations(
        self,
        *,
        project_id: UUID | None,
        limit: int,
        cursor: str | None,
        lifecycle: str = "active",
    ) -> StatePage[Conversation]:
        lifecycle_filters = {
            "active": (conversations.c.deleted_at.is_(None),),
            "deleted": (
                conversations.c.deleted_at.is_not(None),
                conversations.c.purged_at.is_(None),
            ),
            "all": (conversations.c.purged_at.is_(None),),
        }
        try:
            project_filter = (
                (conversations.c.project_id == str(project_id),) if project_id is not None else ()
            )
            filters = (*project_filter, *lifecycle_filters[lifecycle])
        except KeyError as error:
            raise ValueError("unsupported Conversation lifecycle filter") from error
        rows, next_cursor = self._page_rows(
            conversations,
            collection="conversations",
            scope={"project_id": _scope_id(project_id), "lifecycle": lifecycle},
            filters=filters,
            limit=limit,
            cursor=cursor,
        )
        return StatePage(
            items=tuple(self._conversation_from_row(row) for row in rows),
            next_cursor=next_cursor,
        )

    def list_versions(
        self,
        *,
        workspace_id: UUID | None,
        project_id: UUID | None,
        conversation_id: UUID | None,
        task_id: UUID | None,
        limit: int,
        cursor: str | None,
    ) -> StatePage[Version]:
        filters = tuple(
            predicate
            for value, predicate in (
                (workspace_id, versions.c.workspace_id == str(workspace_id)),
                (project_id, versions.c.project_id == str(project_id)),
                (
                    conversation_id,
                    versions.c.source_conversation_id == str(conversation_id),
                ),
                (task_id, versions.c.source_task_id == str(task_id)),
            )
            if value is not None
        )
        scope = {
            "workspace_id": _scope_id(workspace_id),
            "project_id": _scope_id(project_id),
            "conversation_id": _scope_id(conversation_id),
            "task_id": _scope_id(task_id),
        }
        rows, next_cursor = self._page_rows(
            versions,
            collection="versions",
            scope=scope,
            filters=filters,
            limit=limit,
            cursor=cursor,
        )
        return StatePage(
            items=tuple(self._version_from_row(row) for row in rows),
            next_cursor=next_cursor,
        )

    def list_tasks(
        self,
        *,
        project_id: UUID | None,
        conversation_id: UUID | None,
        limit: int,
        cursor: str | None,
    ) -> StatePage[Task]:
        filters = tuple(
            predicate
            for value, predicate in (
                (project_id, tasks.c.project_id == str(project_id)),
                (conversation_id, tasks.c.conversation_id == str(conversation_id)),
            )
            if value is not None
        )
        rows, next_cursor = self._page_rows(
            tasks,
            collection="tasks",
            scope={
                "project_id": _scope_id(project_id),
                "conversation_id": _scope_id(conversation_id),
            },
            filters=filters,
            limit=limit,
            cursor=cursor,
        )
        return StatePage(
            items=tuple(self._task_from_row(row) for row in rows),
            next_cursor=next_cursor,
        )

    def list_approvals(
        self,
        *,
        project_id: UUID | None,
        conversation_id: UUID | None,
        task_id: UUID | None,
        limit: int,
        cursor: str | None,
    ) -> StatePage[Approval]:
        filters = tuple(
            predicate
            for value, predicate in (
                (project_id, tasks.c.project_id == str(project_id)),
                (conversation_id, tasks.c.conversation_id == str(conversation_id)),
                (task_id, approvals.c.task_id == str(task_id)),
            )
            if value is not None
        )
        task_join = approvals.join(
            tasks,
            and_(
                approvals.c.tenant_id == tasks.c.tenant_id,
                approvals.c.task_id == tasks.c.id,
            ),
        )
        rows, next_cursor = self._page_rows(
            approvals,
            collection="approvals",
            scope={
                "project_id": _scope_id(project_id),
                "conversation_id": _scope_id(conversation_id),
                "task_id": _scope_id(task_id),
            },
            filters=filters,
            limit=limit,
            cursor=cursor,
            from_clause=task_join,
        )
        return StatePage(
            items=tuple(self._approval_from_row(row) for row in rows),
            next_cursor=next_cursor,
        )

    def _page_rows(
        self,
        table: Table,
        *,
        collection: str,
        scope: Mapping[str, str | None],
        filters: tuple[Any, ...],
        limit: int,
        cursor: str | None,
        from_clause: Any | None = None,
    ) -> tuple[list[RowMapping], str | None]:
        validate_limit(limit)
        position = decode_cursor(
            cursor,
            collection=collection,
            scope=scope,
        )
        statement = select(table)
        if from_clause is not None:
            statement = statement.select_from(from_clause)
        predicates: list[Any] = [table.c.tenant_id == self._tenant_id, *filters]
        if position is not None:
            predicates.append(
                or_(
                    table.c.created_at > position.created_at,
                    and_(
                        table.c.created_at == position.created_at,
                        table.c.id > str(position.entity_id),
                    ),
                )
            )
        statement = (
            statement.where(*predicates).order_by(table.c.created_at, table.c.id).limit(limit + 1)
        )
        with self._session.read() as connection:
            rows = list(connection.execute(statement).mappings().all())
        page_rows = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            last = page_rows[-1]
            next_cursor = encode_cursor(
                collection=collection,
                created_at=_datetime(last["created_at"]),
                entity_id=UUID(last["id"]),
                scope=scope,
            )
        return page_rows, next_cursor


def _scope_id(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


def _datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
