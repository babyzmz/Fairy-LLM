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
    Workspace,
    WorkspaceType,
)
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.storage.collection_store import CollectionStateStoreMixin
from fairy_core.storage.execution_store import ExecutionStateStoreMixin
from fairy_core.storage.media_store import MediaStateStoreMixin
from fairy_core.storage.media_work_store import MediaWorkStoreMixin
from fairy_core.storage.planning_store import PlanningStateStoreMixin
from fairy_core.storage.schema import (
    approvals,
    changesets,
    checkpoints,
    conversations,
    projects,
    state_metadata,
    tasks,
    versions,
    workspaces,
)


def _uuid(value: str | None) -> UUID | None:
    return UUID(value) if value else None


def _datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _optional_datetime(value: datetime | str | None) -> datetime | None:
    return _datetime(value) if value is not None else None


class SqlAlchemyStateStore(
    CollectionStateStoreMixin,
    ExecutionStateStoreMixin,
    MediaStateStoreMixin,
    MediaWorkStoreMixin,
    PlanningStateStoreMixin,
):
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

    def save_workspace(self, workspace: Workspace) -> None:
        self._upsert(
            workspaces,
            {
                "id": str(workspace.id),
                "active_version_id": (
                    str(workspace.active_version_id) if workspace.active_version_id else None
                ),
                "active_preview_id": (
                    str(workspace.active_preview_id) if workspace.active_preview_id else None
                ),
                "revision": workspace.revision,
                "max_files": workspace.max_files,
                "max_bytes": workspace.max_bytes,
                "created_at": workspace.created_at,
                "updated_at": workspace.updated_at,
            },
        )

    def get_workspace(self, workspace_id: UUID) -> Workspace | None:
        row = self._get_by_id(workspaces, workspace_id)
        return self._workspace_from_row(row) if row is not None else None

    def _ensure_workspace(
        self,
        workspace_id: UUID,
        *,
        active_version_id: UUID | None = None,
        active_preview_id: UUID | None = None,
        revision: int = 0,
    ) -> None:
        if self.get_workspace(workspace_id) is not None:
            return
        self.save_workspace(
            Workspace(
                id=workspace_id,
                active_version_id=active_version_id,
                active_preview_id=active_preview_id,
                revision=revision,
            )
        )

    def save_project(self, project: Project) -> None:
        self._ensure_workspace(
            project.workspace_id,
            active_version_id=project.active_version_id,
            active_preview_id=project.active_preview_id,
            revision=project.revision,
        )
        self._upsert(
            projects,
            {
                "id": str(project.id),
                "name": project.name,
                "residency": project.residency.value,
                "workspace_id": str(project.workspace_id),
                "active_version_id": (
                    str(project.active_version_id) if project.active_version_id else None
                ),
                "active_preview_id": (
                    str(project.active_preview_id) if project.active_preview_id else None
                ),
                "revision": project.revision,
                "pinned_at": project.pinned_at,
                "archived_at": project.archived_at,
                "deleted_at": project.deleted_at,
                "purged_at": project.purged_at,
                "metadata_revision": project.metadata_revision,
                "created_at": project.created_at,
                "updated_at": project.updated_at,
            },
        )

    def update_project_metadata(
        self,
        project: Project,
        *,
        expected_revision: int,
    ) -> None:
        with self._session.write() as connection:
            result = connection.execute(
                update(projects)
                .where(
                    projects.c.tenant_id == self._tenant_id,
                    projects.c.id == str(project.id),
                    projects.c.metadata_revision == expected_revision,
                )
                .values(
                    name=project.name,
                    pinned_at=project.pinned_at,
                    archived_at=project.archived_at,
                    deleted_at=project.deleted_at,
                    purged_at=project.purged_at,
                    metadata_revision=project.metadata_revision,
                    updated_at=project.updated_at,
                )
            )
        if result.rowcount != 1:
            raise VersionConflictError("Project metadata changed concurrently")

    def get_project(self, project_id: UUID) -> Project | None:
        row = self._get_by_id(projects, project_id)
        return self._project_from_row(row) if row is not None else None

    def save_conversation(self, conversation: Conversation) -> None:
        assert conversation.workspace_id is not None
        self._ensure_workspace(conversation.workspace_id)
        self._upsert(
            conversations,
            {
                "id": str(conversation.id),
                "project_id": str(conversation.project_id) if conversation.project_id else None,
                "workspace_id": str(conversation.workspace_id),
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
                "title": conversation.title,
                "pinned_at": conversation.pinned_at,
                "deleted_at": conversation.deleted_at,
                "deleted_by_project_at": conversation.deleted_by_project_at,
                "purged_at": conversation.purged_at,
                "revision": conversation.revision,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
            },
        )

    def update_conversation_metadata(
        self,
        conversation: Conversation,
        *,
        expected_revision: int,
    ) -> None:
        with self._session.write() as connection:
            result = connection.execute(
                update(conversations)
                .where(
                    conversations.c.tenant_id == self._tenant_id,
                    conversations.c.id == str(conversation.id),
                    conversations.c.revision == expected_revision,
                )
                .values(
                    title=conversation.title,
                    pinned_at=conversation.pinned_at,
                    deleted_at=conversation.deleted_at,
                    deleted_by_project_at=conversation.deleted_by_project_at,
                    purged_at=conversation.purged_at,
                    revision=conversation.revision,
                    updated_at=conversation.updated_at,
                )
            )
        if result.rowcount != 1:
            raise VersionConflictError("Conversation metadata changed concurrently")

    def get_conversation(self, conversation_id: UUID) -> Conversation | None:
        row = self._get_by_id(conversations, conversation_id)
        return self._conversation_from_row(row) if row is not None else None

    def save_version(self, version: Version) -> None:
        assert version.workspace_id is not None
        self._ensure_workspace(version.workspace_id)
        self._upsert(
            versions,
            {
                "id": str(version.id),
                "project_id": str(version.project_id) if version.project_id else None,
                "workspace_id": str(version.workspace_id),
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
        return self._version_from_row(row) if row is not None else None

    def save_task(self, task: Task, *, idempotency_key: str | None = None) -> None:
        assert task.workspace_id is not None
        self._ensure_workspace(task.workspace_id)
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
                "workspace_id": str(task.workspace_id),
                "conversation_id": str(task.conversation_id),
                "user_request": task.user_request,
                "operation_mode": task.operation_mode.value,
                "base_version_id": str(task.base_version_id) if task.base_version_id else None,
                "target_version_id": (
                    str(task.target_version_id) if task.target_version_id else None
                ),
                "execution_target": task.execution_target,
                "memory_snapshot_id": (
                    str(task.memory_snapshot_id) if task.memory_snapshot_id else None
                ),
                "memory_snapshot_hash": task.memory_snapshot_hash,
                "knowledge_snapshot_id": (
                    str(task.knowledge_snapshot_id) if task.knowledge_snapshot_id else None
                ),
                "knowledge_snapshot_hash": task.knowledge_snapshot_hash,
                "harness_manifest_id": (
                    str(task.harness_manifest_id) if task.harness_manifest_id else None
                ),
                "harness_manifest_hash": task.harness_manifest_hash,
                "status": task.status.value,
                "display_title": task.display_title,
                "pinned_at": task.pinned_at,
                "metadata_revision": task.metadata_revision,
                "idempotency_key": idempotency_key,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
            },
        )

    def update_task_metadata(self, task: Task, *, expected_revision: int) -> None:
        with self._session.write() as connection:
            result = connection.execute(
                update(tasks)
                .where(
                    tasks.c.tenant_id == self._tenant_id,
                    tasks.c.id == str(task.id),
                    tasks.c.metadata_revision == expected_revision,
                )
                .values(
                    display_title=task.display_title,
                    pinned_at=task.pinned_at,
                    metadata_revision=task.metadata_revision,
                    status=task.status.value,
                    updated_at=task.updated_at,
                )
            )
        if result.rowcount != 1:
            raise VersionConflictError("Task metadata changed concurrently")

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
                "project_id": str(changeset.project_id) if changeset.project_id else None,
                "workspace_id": str(changeset.workspace_id),
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

    def get_changeset_for_update(self, changeset_id: UUID) -> Changeset | None:
        """Serialize receipt publication with approval/application settlement."""
        predicates = (
            changesets.c.tenant_id == self._tenant_id, changesets.c.id == str(changeset_id),
        )
        with self._session.write() as connection:
            if connection.dialect.name == "sqlite":
                # SQLite SELECT does not acquire a row lock. A no-op write starts
                # the writer transaction without changing the domain timestamp.
                connection.execute(update(changesets).where(*predicates).values(
                    updated_at=changesets.c.updated_at,
                ))
            row = connection.execute(
                select(changesets).where(*predicates).with_for_update(),
            ).mappings().first()
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
                "tool_invocation_id": (
                    str(approval.tool_invocation_id) if approval.tool_invocation_id else None
                ),
                "requested_by": approval.requested_by,
                "reason": approval.reason,
                "decision": approval.decision.value,
                "decided_by": approval.decided_by,
                "created_at": approval.created_at,
                "decided_at": approval.decided_at,
            },
        )

    def update_approval(
        self,
        approval: Approval,
        *,
        expected_decision: ApprovalDecision,
    ) -> None:
        with self._session.write() as connection:
            result = connection.execute(
                update(approvals)
                .where(
                    approvals.c.tenant_id == self._tenant_id,
                    approvals.c.id == str(approval.id),
                    approvals.c.task_id == str(approval.task_id),
                    approvals.c.command_run_id == str(approval.command_run_id),
                    approvals.c.changeset_id
                    == (str(approval.changeset_id) if approval.changeset_id else None),
                    approvals.c.tool_invocation_id
                    == (str(approval.tool_invocation_id) if approval.tool_invocation_id else None),
                    approvals.c.decision == expected_decision.value,
                )
                .values(
                    decision=approval.decision.value,
                    decided_by=approval.decided_by,
                    decided_at=approval.decided_at,
                )
            )
        if result.rowcount != 1:
            raise InvalidTransitionError("Approval changed concurrently")

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

    def find_approval_by_tool_invocation_id(
        self,
        tool_invocation_id: UUID,
    ) -> Approval | None:
        row = self._first(
            select(approvals).where(
                approvals.c.tenant_id == self._tenant_id,
                approvals.c.tool_invocation_id == str(tool_invocation_id),
            )
        )
        return self._approval_from_row(row) if row is not None else None

    def find_approval_by_command_run_id(self, command_run_id: UUID) -> Approval | None:
        row = self._first(
            select(approvals).where(
                approvals.c.tenant_id == self._tenant_id,
                approvals.c.command_run_id == str(command_run_id),
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
                "evidence_artifact_ids": [
                    str(artifact_id) for artifact_id in checkpoint.evidence_artifact_ids
                ],
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
            evidence_artifact_ids=tuple(UUID(value) for value in row["evidence_artifact_ids"]),
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
            workspace_id=UUID(row["workspace_id"]),
            active_version_id=_uuid(row["active_version_id"]),
            active_preview_id=_uuid(row["active_preview_id"]),
            revision=int(row["revision"]),
            pinned_at=_optional_datetime(row["pinned_at"]),
            archived_at=_optional_datetime(row["archived_at"]),
            deleted_at=_optional_datetime(row["deleted_at"]),
            purged_at=_optional_datetime(row["purged_at"]),
            metadata_revision=int(row["metadata_revision"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _conversation_from_row(row: Mapping[str, Any]) -> Conversation:
        return Conversation(
            id=UUID(row["id"]),
            project_id=_uuid(row["project_id"]),
            workspace_id=UUID(row["workspace_id"]),
            workspace_type=WorkspaceType(row["workspace_type"]),
            base_version_id=_uuid(row["base_version_id"]),
            active_draft_version_id=_uuid(row["active_draft_version_id"]),
            active_task_id=_uuid(row["active_task_id"]),
            active_preview_id=_uuid(row["active_preview_id"]),
            title=row["title"],
            pinned_at=_optional_datetime(row["pinned_at"]),
            deleted_at=_optional_datetime(row["deleted_at"]),
            deleted_by_project_at=_optional_datetime(row["deleted_by_project_at"]),
            purged_at=_optional_datetime(row["purged_at"]),
            revision=int(row["revision"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _version_from_row(row: Mapping[str, Any]) -> Version:
        return Version(
            id=UUID(row["id"]),
            project_id=_uuid(row["project_id"]),
            workspace_id=UUID(row["workspace_id"]),
            source_conversation_id=_uuid(row["source_conversation_id"]),
            source_task_id=_uuid(row["source_task_id"]),
            parent_version_id=_uuid(row["parent_version_id"]),
            project_root=Path(row["project_root"]),
            visibility=VersionVisibility(row["visibility"]),
            created_at=_datetime(row["created_at"]),
        )

    @staticmethod
    def _task_from_row(row: Mapping[str, Any]) -> Task:
        return Task(
            id=UUID(row["id"]),
            project_id=_uuid(row["project_id"]),
            workspace_id=UUID(row["workspace_id"]),
            conversation_id=UUID(row["conversation_id"]),
            user_request=row["user_request"],
            operation_mode=OperationMode(row["operation_mode"]),
            base_version_id=_uuid(row["base_version_id"]),
            execution_target=row["execution_target"],
            target_version_id=_uuid(row["target_version_id"]),
            memory_snapshot_id=_uuid(row["memory_snapshot_id"]),
            memory_snapshot_hash=row["memory_snapshot_hash"],
            knowledge_snapshot_id=_uuid(row["knowledge_snapshot_id"]),
            knowledge_snapshot_hash=row["knowledge_snapshot_hash"],
            harness_manifest_id=_uuid(row["harness_manifest_id"]),
            harness_manifest_hash=row["harness_manifest_hash"],
            status=TaskStatus(row["status"]),
            display_title=row["display_title"],
            pinned_at=_optional_datetime(row["pinned_at"]),
            metadata_revision=int(row["metadata_revision"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _workspace_from_row(row: Mapping[str, Any]) -> Workspace:
        return Workspace(
            id=UUID(row["id"]),
            active_version_id=_uuid(row["active_version_id"]),
            active_preview_id=_uuid(row["active_preview_id"]),
            revision=int(row["revision"]),
            max_files=int(row["max_files"]),
            max_bytes=int(row["max_bytes"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _changeset_from_row(row: Mapping[str, Any]) -> Changeset:
        return Changeset(
            id=UUID(row["id"]),
            project_id=_uuid(row["project_id"]),
            workspace_id=UUID(row["workspace_id"]),
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
            tool_invocation_id=_uuid(row["tool_invocation_id"]),
            requested_by=row["requested_by"],
            reason=row["reason"],
            decision=ApprovalDecision(row["decision"]),
            decided_by=row["decided_by"],
            created_at=_datetime(row["created_at"]),
            decided_at=_datetime(row["decided_at"]) if row["decided_at"] else None,
        )
