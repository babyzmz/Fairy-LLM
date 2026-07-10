from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Table, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine, RowMapping

from fairy_core.domain.errors import InvalidTransitionError, VersionConflictError
from fairy_core.domain.execution import (
    Approval,
    ApprovalDecision,
    Changeset,
    ChangesetStatus,
    Checkpoint,
)
from fairy_core.domain.models import (
    Conversation,
    OperationMode,
    Project,
    ProjectResidency,
    Task,
    TaskStatus,
    Version,
    VersionVisibility,
    WorkspaceType,
)
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.storage.schema import (
    approvals,
    changesets,
    checkpoints,
    conversations,
    projects,
    state_metadata,
    tasks,
    versions,
)


def _uuid(value: str | None) -> UUID | None:
    return UUID(value) if value else None


def _datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class SqlAlchemyStateStore:
    """Tenant-scoped Core state persisted through a caller-owned SQLAlchemy engine."""

    def __init__(
        self,
        bind: Engine | Connection,
        *,
        tenant_id: str,
        initialize_schema: bool = False,
        owns_engine: bool = False,
    ) -> None:
        normalized_tenant = normalize_tenant_id(tenant_id)
        dialect_name = bind.dialect.name
        if dialect_name not in {"postgresql", "sqlite"}:
            raise ValueError(f"unsupported state-store dialect: {dialect_name}")
        if initialize_schema and dialect_name != "sqlite":
            raise ValueError("PostgreSQL schemas must be initialized through Alembic")
        self._tenant_id = normalized_tenant
        self._session = SqlAlchemySession(bind, owns_engine=owns_engine)
        if initialize_schema:
            state_metadata.create_all(bind)

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def close(self) -> None:
        self._session.close()

    def save_project(self, project: Project) -> None:
        self._upsert(
            projects,
            {
                "id": str(project.id),
                "name": project.name,
                "residency": project.residency.value,
                "active_version_id": (
                    str(project.active_version_id) if project.active_version_id else None
                ),
                "active_preview_id": (
                    str(project.active_preview_id) if project.active_preview_id else None
                ),
                "revision": project.revision,
                "created_at": project.created_at,
                "updated_at": project.updated_at,
            },
        )

    def get_project(self, project_id: UUID) -> Project | None:
        row = self._get_by_id(projects, project_id)
        return self._project_from_row(row) if row is not None else None

    def save_conversation(self, conversation: Conversation) -> None:
        self._upsert(
            conversations,
            {
                "id": str(conversation.id),
                "project_id": str(conversation.project_id) if conversation.project_id else None,
                "workspace_type": conversation.workspace_type.value,
                "base_version_id": (
                    str(conversation.base_version_id) if conversation.base_version_id else None
                ),
                "active_draft_version_id": (
                    str(conversation.active_draft_version_id)
                    if conversation.active_draft_version_id
                    else None
                ),
                "active_task_id": (
                    str(conversation.active_task_id) if conversation.active_task_id else None
                ),
                "active_preview_id": (
                    str(conversation.active_preview_id) if conversation.active_preview_id else None
                ),
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
            },
        )

    def get_conversation(self, conversation_id: UUID) -> Conversation | None:
        row = self._get_by_id(conversations, conversation_id)
        if row is None:
            return None
        return Conversation(
            id=UUID(row["id"]),
            project_id=_uuid(row["project_id"]),
            workspace_type=WorkspaceType(row["workspace_type"]),
            base_version_id=_uuid(row["base_version_id"]),
            active_draft_version_id=_uuid(row["active_draft_version_id"]),
            active_task_id=_uuid(row["active_task_id"]),
            active_preview_id=_uuid(row["active_preview_id"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    def save_version(self, version: Version) -> None:
        self._upsert(
            versions,
            {
                "id": str(version.id),
                "project_id": str(version.project_id),
                "source_conversation_id": (
                    str(version.source_conversation_id) if version.source_conversation_id else None
                ),
                "source_task_id": (str(version.source_task_id) if version.source_task_id else None),
                "parent_version_id": (
                    str(version.parent_version_id) if version.parent_version_id else None
                ),
                "project_root": str(version.project_root),
                "visibility": version.visibility.value,
                "created_at": version.created_at,
            },
        )

    def get_version(self, version_id: UUID) -> Version | None:
        row = self._get_by_id(versions, version_id)
        if row is None:
            return None
        return Version(
            id=UUID(row["id"]),
            project_id=UUID(row["project_id"]),
            source_conversation_id=_uuid(row["source_conversation_id"]),
            source_task_id=_uuid(row["source_task_id"]),
            parent_version_id=_uuid(row["parent_version_id"]),
            project_root=Path(row["project_root"]),
            visibility=VersionVisibility(row["visibility"]),
            created_at=_datetime(row["created_at"]),
        )

    def save_task(self, task: Task, *, idempotency_key: str | None = None) -> None:
        if idempotency_key is None:
            with self._session.read() as connection:
                existing = connection.execute(
                    select(tasks.c.idempotency_key).where(
                        tasks.c.tenant_id == self._tenant_id,
                        tasks.c.id == str(task.id),
                    )
                ).first()
            if existing is None:
                raise ValueError("idempotency_key is required for a new task")
            idempotency_key = str(existing.idempotency_key)
        self._upsert(
            tasks,
            {
                "id": str(task.id),
                "project_id": str(task.project_id) if task.project_id else None,
                "conversation_id": str(task.conversation_id),
                "user_request": task.user_request,
                "operation_mode": task.operation_mode.value,
                "base_version_id": str(task.base_version_id) if task.base_version_id else None,
                "target_version_id": (
                    str(task.target_version_id) if task.target_version_id else None
                ),
                "execution_target": task.execution_target,
                "status": task.status.value,
                "idempotency_key": idempotency_key,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
            },
        )

    def get_task(self, task_id: UUID) -> Task | None:
        row = self._get_by_id(tasks, task_id)
        return self._task_from_row(row) if row is not None else None

    def find_task_by_idempotency_key(self, idempotency_key: str) -> Task | None:
        row = self._first(
            select(tasks).where(
                tasks.c.tenant_id == self._tenant_id,
                tasks.c.idempotency_key == idempotency_key,
            )
        )
        return self._task_from_row(row) if row is not None else None

    def save_changeset(self, changeset: Changeset) -> None:
        self._upsert(
            changesets,
            {
                "id": str(changeset.id),
                "project_id": str(changeset.project_id),
                "conversation_id": str(changeset.conversation_id),
                "task_id": str(changeset.task_id),
                "version_id": str(changeset.version_id),
                "files": list(changeset.files),
                "patches": list(changeset.patches),
                "reason": changeset.reason,
                "risk_level": changeset.risk_level,
                "idempotency_key": changeset.idempotency_key,
                "status": changeset.status.value,
                "approval_decision": changeset.approval_decision.value,
                "created_at": changeset.created_at,
                "updated_at": changeset.updated_at,
            },
        )

    def get_changeset(self, changeset_id: UUID) -> Changeset | None:
        row = self._get_by_id(changesets, changeset_id)
        return self._changeset_from_row(row) if row is not None else None

    def find_changeset_by_idempotency_key(self, idempotency_key: str) -> Changeset | None:
        row = self._first(
            select(changesets).where(
                changesets.c.tenant_id == self._tenant_id,
                changesets.c.idempotency_key == idempotency_key,
            )
        )
        return self._changeset_from_row(row) if row is not None else None

    def changesets_for_task(self, task_id: UUID) -> list[Changeset]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(changesets)
                    .where(
                        changesets.c.tenant_id == self._tenant_id,
                        changesets.c.task_id == str(task_id),
                    )
                    .order_by(changesets.c.created_at, changesets.c.id)
                )
                .mappings()
                .all()
            )
        return [self._changeset_from_row(row) for row in rows]

    def save_approval(self, approval: Approval) -> None:
        self._upsert(
            approvals,
            {
                "id": str(approval.id),
                "task_id": str(approval.task_id),
                "command_run_id": str(approval.command_run_id),
                "changeset_id": str(approval.changeset_id) if approval.changeset_id else None,
                "requested_by": approval.requested_by,
                "reason": approval.reason,
                "decision": approval.decision.value,
                "decided_by": approval.decided_by,
                "created_at": approval.created_at,
                "decided_at": approval.decided_at,
            },
        )

    def get_approval(self, approval_id: UUID) -> Approval | None:
        row = self._get_by_id(approvals, approval_id)
        return self._approval_from_row(row) if row is not None else None

    def find_approval_by_changeset_id(self, changeset_id: UUID) -> Approval | None:
        row = self._first(
            select(approvals).where(
                approvals.c.tenant_id == self._tenant_id,
                approvals.c.changeset_id == str(changeset_id),
            )
        )
        return self._approval_from_row(row) if row is not None else None

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        self._upsert(
            checkpoints,
            {
                "id": str(checkpoint.id),
                "task_id": str(checkpoint.task_id),
                "version_id": str(checkpoint.version_id),
                "changed_files": list(checkpoint.changed_files),
                "command_run_ids": [str(run_id) for run_id in checkpoint.command_run_ids],
                "preview_artifact_id": (
                    str(checkpoint.preview_artifact_id) if checkpoint.preview_artifact_id else None
                ),
                "created_at": checkpoint.created_at,
            },
        )

    def get_checkpoint(self, checkpoint_id: UUID) -> Checkpoint | None:
        row = self._get_by_id(checkpoints, checkpoint_id)
        if row is None:
            return None
        return Checkpoint(
            id=UUID(row["id"]),
            task_id=UUID(row["task_id"]),
            version_id=UUID(row["version_id"]),
            changed_files=tuple(row["changed_files"]),
            command_run_ids=tuple(UUID(value) for value in row["command_run_ids"]),
            preview_artifact_id=_uuid(row["preview_artifact_id"]),
            created_at=_datetime(row["created_at"]),
        )

    def accept_version(
        self,
        *,
        project_id: UUID,
        version_id: UUID,
        expected_revision: int,
    ) -> Project:
        with self._session.write() as connection:
            version_row = connection.execute(
                select(versions.c.project_id).where(
                    versions.c.tenant_id == self._tenant_id,
                    versions.c.id == str(version_id),
                )
            ).first()
            if version_row is None or version_row.project_id != str(project_id):
                raise KeyError(f"version does not belong to project: {version_id}")
            current = (
                connection.execute(
                    select(projects).where(
                        projects.c.tenant_id == self._tenant_id,
                        projects.c.id == str(project_id),
                    )
                )
                .mappings()
                .first()
            )
            if current is None:
                raise KeyError(f"project not found: {project_id}")
            result = connection.execute(
                update(projects)
                .where(
                    projects.c.tenant_id == self._tenant_id,
                    projects.c.id == str(project_id),
                    projects.c.revision == expected_revision,
                )
                .values(
                    active_version_id=str(version_id),
                    revision=expected_revision + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            if result.rowcount != 1:
                raise VersionConflictError(
                    f"expected project revision {expected_revision}, current revision has changed"
                )
        accepted = self.get_project(project_id)
        assert accepted is not None
        return accepted

    def reserve_version_discard(
        self,
        *,
        project_id: UUID,
        version_id: UUID,
        expected_revision: int,
    ) -> Project:
        now = datetime.now(UTC)
        with self._session.write() as connection:
            version_row = connection.execute(
                select(versions.c.project_id).where(
                    versions.c.tenant_id == self._tenant_id,
                    versions.c.id == str(version_id),
                )
            ).first()
            if version_row is None or version_row.project_id != str(project_id):
                raise KeyError(f"version does not belong to project: {version_id}")
            current = (
                connection.execute(
                    select(projects).where(
                        projects.c.tenant_id == self._tenant_id,
                        projects.c.id == str(project_id),
                    )
                )
                .mappings()
                .first()
            )
            if current is None:
                raise KeyError(f"project not found: {project_id}")
            if current["active_version_id"] == str(version_id):
                raise InvalidTransitionError("the Active Version cannot be discarded")
            result = connection.execute(
                update(projects)
                .where(
                    projects.c.tenant_id == self._tenant_id,
                    projects.c.id == str(project_id),
                    projects.c.revision == expected_revision,
                    or_(
                        projects.c.active_version_id.is_(None),
                        projects.c.active_version_id != str(version_id),
                    ),
                )
                .values(revision=expected_revision + 1, updated_at=now)
            )
            if result.rowcount != 1:
                raise VersionConflictError(
                    f"expected project revision {expected_revision}, current revision has changed"
                )
            updated = (
                connection.execute(
                    select(projects).where(
                        projects.c.tenant_id == self._tenant_id,
                        projects.c.id == str(project_id),
                    )
                )
                .mappings()
                .one()
            )
        return self._project_from_row(updated)

    def _get_by_id(self, table: Table, identifier: UUID) -> RowMapping | None:
        return self._first(
            select(table).where(
                table.c.tenant_id == self._tenant_id,
                table.c.id == str(identifier),
            )
        )

    def _first(self, statement: Any) -> RowMapping | None:
        with self._session.read() as connection:
            return connection.execute(statement).mappings().first()

    def _upsert(self, table: Table, values: dict[str, object]) -> None:
        scoped_values = {"tenant_id": self._tenant_id, **values}
        updates = {
            key: value for key, value in scoped_values.items() if key not in {"tenant_id", "id"}
        }
        if self._session.dialect_name == "postgresql":
            statement = postgresql_insert(table).values(**scoped_values)
            statement = statement.on_conflict_do_update(
                index_elements=[table.c.tenant_id, table.c.id],
                set_=updates,
            )
        elif self._session.dialect_name == "sqlite":
            statement = sqlite_insert(table).values(**scoped_values)
            statement = statement.on_conflict_do_update(
                index_elements=[table.c.tenant_id, table.c.id],
                set_=updates,
            )
        with self._session.write() as connection:
            connection.execute(statement)

    @staticmethod
    def _project_from_row(row: Mapping[str, Any]) -> Project:
        return Project(
            id=UUID(row["id"]),
            name=row["name"],
            residency=ProjectResidency(row["residency"]),
            active_version_id=_uuid(row["active_version_id"]),
            active_preview_id=_uuid(row["active_preview_id"]),
            revision=int(row["revision"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _task_from_row(row: Mapping[str, Any]) -> Task:
        return Task(
            id=UUID(row["id"]),
            project_id=_uuid(row["project_id"]),
            conversation_id=UUID(row["conversation_id"]),
            user_request=row["user_request"],
            operation_mode=OperationMode(row["operation_mode"]),
            base_version_id=_uuid(row["base_version_id"]),
            execution_target=row["execution_target"],
            target_version_id=_uuid(row["target_version_id"]),
            status=TaskStatus(row["status"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _changeset_from_row(row: Mapping[str, Any]) -> Changeset:
        return Changeset(
            id=UUID(row["id"]),
            project_id=UUID(row["project_id"]),
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            version_id=UUID(row["version_id"]),
            files=tuple(row["files"]),
            patches=tuple(row["patches"]),
            reason=row["reason"],
            risk_level=row["risk_level"],
            idempotency_key=row["idempotency_key"],
            status=ChangesetStatus(row["status"]),
            approval_decision=ApprovalDecision(row["approval_decision"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _approval_from_row(row: Mapping[str, Any]) -> Approval:
        return Approval(
            id=UUID(row["id"]),
            task_id=UUID(row["task_id"]),
            command_run_id=UUID(row["command_run_id"]),
            changeset_id=_uuid(row["changeset_id"]),
            requested_by=row["requested_by"],
            reason=row["reason"],
            decision=ApprovalDecision(row["decision"]),
            decided_by=row["decided_by"],
            created_at=_datetime(row["created_at"]),
            decided_at=_datetime(row["decided_at"]) if row["decided_at"] else None,
        )
