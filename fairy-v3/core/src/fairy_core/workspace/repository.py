from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine, RowMapping

from fairy_core.domain.errors import IdempotencyConflictError
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.storage.schema import project_indexes, task_workspaces
from fairy_core.workspace.models import ProjectFile, ProjectIndex, TaskWorkspace


def _uuid(value: UUID | str | None) -> UUID | None:
    return UUID(str(value)) if value is not None else None


def _datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class SqlAlchemyWorkspaceRepository:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str) -> None:
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._session = SqlAlchemySession(bind)

    def bind_once(
        self,
        *,
        task_id: UUID | str,
        project_id: UUID | str | None,
        workspace_id: UUID | str | None = None,
        conversation_id: UUID | str,
        version_id: UUID | str | None,
        root: Path,
        editable_files: tuple[str, ...],
        reference_files: tuple[str, ...],
        constraints: dict[str, Any],
    ) -> TaskWorkspace:
        candidate = TaskWorkspace(
            task_id=_require_uuid(task_id),
            project_id=_uuid(project_id),
            workspace_id=_uuid(workspace_id),
            conversation_id=_require_uuid(conversation_id),
            version_id=_uuid(version_id),
            root=root,
            editable_files=editable_files,
            reference_files=reference_files,
            constraints=constraints,
        )
        values = self._workspace_values(candidate)
        statement = (
            self._insert(task_workspaces)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["tenant_id", "task_id"])
        )
        with self._session.write() as connection:
            inserted = connection.execute(statement).rowcount == 1
        if inserted:
            return candidate
        existing = self.get(candidate.task_id)
        if existing is not None and self._same_workspace(existing, candidate):
            return existing
        raise IdempotencyConflictError("Task Workspace is already bound differently")

    def get(self, task_id: UUID | str) -> TaskWorkspace | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(task_workspaces).where(
                        task_workspaces.c.tenant_id == self._tenant_id,
                        task_workspaces.c.task_id == str(task_id),
                    )
                )
                .mappings()
                .first()
            )
        return self._workspace_from_row(row) if row is not None else None

    def _insert(self, table):
        return (
            postgresql_insert(table)
            if self._session.dialect_name == "postgresql"
            else sqlite_insert(table)
        )

    def _workspace_values(self, workspace: TaskWorkspace) -> dict[str, object]:
        return {
            "tenant_id": self._tenant_id,
            "task_id": str(workspace.task_id),
            "project_id": str(workspace.project_id) if workspace.project_id else None,
            "workspace_id": str(workspace.workspace_id),
            "conversation_id": str(workspace.conversation_id),
            "version_id": str(workspace.version_id) if workspace.version_id else None,
            "root": str(workspace.root),
            "editable_files": list(workspace.editable_files),
            "reference_files": list(workspace.reference_files),
            "constraints": dict(workspace.constraints),
            "generation": workspace.generation,
            "created_at": workspace.created_at,
        }

    @staticmethod
    def _workspace_from_row(row: RowMapping) -> TaskWorkspace:
        return TaskWorkspace(
            task_id=UUID(row["task_id"]),
            project_id=_uuid(row["project_id"]),
            workspace_id=UUID(row["workspace_id"]),
            conversation_id=UUID(row["conversation_id"]),
            version_id=_uuid(row["version_id"]),
            root=Path(row["root"]),
            editable_files=tuple(row["editable_files"]),
            reference_files=tuple(row["reference_files"]),
            constraints=dict(row["constraints"]),
            generation=int(row["generation"]),
            created_at=_datetime(row["created_at"]),
        )

    @staticmethod
    def _same_workspace(left: TaskWorkspace, right: TaskWorkspace) -> bool:
        return (
            left.task_id == right.task_id
            and left.project_id == right.project_id
            and left.workspace_id == right.workspace_id
            and left.conversation_id == right.conversation_id
            and left.version_id == right.version_id
            and left.root == right.root
            and left.editable_files == right.editable_files
            and left.reference_files == right.reference_files
            and dict(left.constraints) == dict(right.constraints)
        )


class SqlAlchemyProjectIndexRepository:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str) -> None:
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._session = SqlAlchemySession(bind)

    def get(self, version_id: UUID | str) -> ProjectIndex | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(project_indexes).where(
                        project_indexes.c.tenant_id == self._tenant_id,
                        project_indexes.c.version_id == str(version_id),
                    )
                )
                .mappings()
                .first()
            )
        return self._index_from_row(row) if row is not None else None

    def replace_generation(
        self,
        index: ProjectIndex,
        *,
        expected_generation: int,
    ) -> ProjectIndex:
        if expected_generation < 0:
            raise ValueError("expected Project Index generation cannot be negative")
        existing = self.get(index.version_id)
        actual_generation = existing.generation if existing is not None else 0
        if actual_generation != expected_generation:
            if existing is not None and self._same_index(existing, index):
                return existing
            raise IdempotencyConflictError("Project Index generation changed concurrently")
        if index.generation != expected_generation + 1:
            raise ValueError("Project Index generation must advance exactly once")
        values = self._index_values(index)
        if expected_generation == 0:
            statement = (
                self._insert(project_indexes)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["tenant_id", "version_id"])
            )
        else:
            statement = (
                update(project_indexes)
                .where(
                    project_indexes.c.tenant_id == self._tenant_id,
                    project_indexes.c.version_id == str(index.version_id),
                    project_indexes.c.workspace_id == str(index.workspace_id),
                    project_indexes.c.generation == expected_generation,
                )
                .values(
                    generation=index.generation,
                    source_hash=index.source_hash,
                    files=values["files"],
                    updated_at=index.created_at,
                )
            )
        with self._session.write() as connection:
            changed = connection.execute(statement).rowcount == 1
        if not changed:
            existing = self.get(index.version_id)
            if existing is not None and self._same_index(existing, index):
                return existing
            raise IdempotencyConflictError("Project Index generation changed concurrently")
        return index

    def _insert(self, table):
        return (
            postgresql_insert(table)
            if self._session.dialect_name == "postgresql"
            else sqlite_insert(table)
        )

    def _index_values(self, index: ProjectIndex) -> dict[str, object]:
        return {
            "tenant_id": self._tenant_id,
            "version_id": str(index.version_id),
            "project_id": str(index.project_id) if index.project_id else None,
            "workspace_id": str(index.workspace_id),
            "generation": index.generation,
            "source_hash": index.source_hash,
            "files": [self._file_values(item) for item in index.files],
            "created_at": index.created_at,
            "updated_at": index.created_at,
        }

    @staticmethod
    def _file_values(item: ProjectFile) -> dict[str, object]:
        return {
            "path": item.path,
            "byte_length": item.byte_length,
            "content_hash": item.content_hash,
            "kind": item.kind,
            "language": item.language,
            "imports": list(item.imports),
            "exports": list(item.exports),
            "symbols": list(item.symbols),
            "summary": dict(item.summary),
        }

    @classmethod
    def _index_from_row(cls, row: RowMapping) -> ProjectIndex:
        return ProjectIndex(
            project_id=_uuid(row["project_id"]),
            workspace_id=UUID(row["workspace_id"]),
            version_id=UUID(row["version_id"]),
            generation=int(row["generation"]),
            source_hash=row["source_hash"],
            files=tuple(ProjectFile(**dict(item)) for item in row["files"]),
            created_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _same_index(left: ProjectIndex, right: ProjectIndex) -> bool:
        return (
            left.project_id == right.project_id
            and left.workspace_id == right.workspace_id
            and left.version_id == right.version_id
            and left.generation == right.generation
            and left.source_hash == right.source_hash
            and left.files == right.files
        )


def _require_uuid(value: UUID | str) -> UUID:
    return UUID(str(value))


__all__ = ["SqlAlchemyProjectIndexRepository", "SqlAlchemyWorkspaceRepository"]
