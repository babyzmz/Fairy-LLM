from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from fairy_core.domain.errors import VersionConflictError
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

metadata = MetaData()

projects = Table(
    "projects",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("residency", String, nullable=False),
    Column("active_version_id", String),
    Column("active_preview_id", String),
    Column("revision", Integer, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

conversations = Table(
    "conversations",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", String),
    Column("workspace_type", String, nullable=False),
    Column("base_version_id", String),
    Column("active_draft_version_id", String),
    Column("active_task_id", String),
    Column("active_preview_id", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

versions = Table(
    "versions",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", String, nullable=False),
    Column("source_conversation_id", String),
    Column("source_task_id", String),
    Column("parent_version_id", String),
    Column("project_root", String, nullable=False),
    Column("visibility", String, nullable=False),
    Column("created_at", String, nullable=False),
)

tasks = Table(
    "tasks",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", String),
    Column("conversation_id", String, nullable=False),
    Column("user_request", String, nullable=False),
    Column("operation_mode", String, nullable=False),
    Column("base_version_id", String),
    Column("target_version_id", String),
    Column("execution_target", String, nullable=False),
    Column("status", String, nullable=False),
    Column("idempotency_key", String, nullable=False, unique=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

changesets = Table(
    "changesets",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", String, nullable=False),
    Column("conversation_id", String, nullable=False),
    Column("task_id", String, nullable=False),
    Column("version_id", String, nullable=False),
    Column("files_json", String, nullable=False),
    Column("patches_json", String, nullable=False),
    Column("reason", String, nullable=False),
    Column("risk_level", String, nullable=False),
    Column("idempotency_key", String, nullable=False, unique=True),
    Column("status", String, nullable=False),
    Column("approval_decision", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

approvals = Table(
    "approvals",
    metadata,
    Column("id", String, primary_key=True),
    Column("task_id", String, nullable=False),
    Column("command_run_id", String, nullable=False),
    Column("changeset_id", String),
    Column("requested_by", String, nullable=False),
    Column("reason", String, nullable=False),
    Column("decision", String, nullable=False),
    Column("decided_by", String),
    Column("created_at", String, nullable=False),
    Column("decided_at", String),
)

checkpoints = Table(
    "checkpoints",
    metadata,
    Column("id", String, primary_key=True),
    Column("task_id", String, nullable=False),
    Column("version_id", String, nullable=False),
    Column("changed_files_json", String, nullable=False),
    Column("command_run_ids_json", String, nullable=False),
    Column("preview_artifact_id", String),
    Column("created_at", String, nullable=False),
)


def _uuid(value: str | None) -> UUID | None:
    return UUID(value) if value else None


class SqliteStateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._engine: Engine = create_engine(f"sqlite+pysqlite:///{path}", future=True)
        metadata.create_all(self._engine)

    def close(self) -> None:
        self._engine.dispose()

    def save_project(self, project: Project) -> None:
        values = {
            "id": str(project.id),
            "name": project.name,
            "residency": project.residency.value,
            "active_version_id": str(project.active_version_id)
            if project.active_version_id
            else None,
            "active_preview_id": str(project.active_preview_id)
            if project.active_preview_id
            else None,
            "revision": project.revision,
            "created_at": project.created_at.isoformat(),
            "updated_at": project.updated_at.isoformat(),
        }
        self._upsert(projects, values)

    def get_project(self, project_id: UUID) -> Project | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(projects).where(projects.c.id == str(project_id)))
                .mappings()
                .first()
            )
        if row is None:
            return None
        return Project(
            id=UUID(row["id"]),
            name=row["name"],
            residency=ProjectResidency(row["residency"]),
            active_version_id=_uuid(row["active_version_id"]),
            active_preview_id=_uuid(row["active_preview_id"]),
            revision=int(row["revision"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def save_conversation(self, conversation: Conversation) -> None:
        values = {
            "id": str(conversation.id),
            "project_id": str(conversation.project_id) if conversation.project_id else None,
            "workspace_type": conversation.workspace_type.value,
            "base_version_id": str(conversation.base_version_id)
            if conversation.base_version_id
            else None,
            "active_draft_version_id": (
                str(conversation.active_draft_version_id)
                if conversation.active_draft_version_id
                else None
            ),
            "active_task_id": str(conversation.active_task_id)
            if conversation.active_task_id
            else None,
            "active_preview_id": (
                str(conversation.active_preview_id) if conversation.active_preview_id else None
            ),
            "created_at": conversation.created_at.isoformat(),
            "updated_at": conversation.updated_at.isoformat(),
        }
        self._upsert(conversations, values)

    def get_conversation(self, conversation_id: UUID) -> Conversation | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(conversations).where(conversations.c.id == str(conversation_id))
                )
                .mappings()
                .first()
            )
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
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def save_version(self, version: Version) -> None:
        values = {
            "id": str(version.id),
            "project_id": str(version.project_id),
            "source_conversation_id": (
                str(version.source_conversation_id) if version.source_conversation_id else None
            ),
            "source_task_id": str(version.source_task_id) if version.source_task_id else None,
            "parent_version_id": str(version.parent_version_id)
            if version.parent_version_id
            else None,
            "project_root": str(version.project_root),
            "visibility": version.visibility.value,
            "created_at": version.created_at.isoformat(),
        }
        self._upsert(versions, values)

    def get_version(self, version_id: UUID) -> Version | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(versions).where(versions.c.id == str(version_id)))
                .mappings()
                .first()
            )
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
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def save_task(self, task: Task, *, idempotency_key: str | None = None) -> None:
        if idempotency_key is None:
            with self._engine.connect() as connection:
                existing = connection.execute(
                    select(tasks.c.idempotency_key).where(tasks.c.id == str(task.id))
                ).first()
            if existing is None:
                raise ValueError("idempotency_key is required for a new task")
            idempotency_key = str(existing.idempotency_key)
        values = {
            "id": str(task.id),
            "project_id": str(task.project_id) if task.project_id else None,
            "conversation_id": str(task.conversation_id),
            "user_request": task.user_request,
            "operation_mode": task.operation_mode.value,
            "base_version_id": str(task.base_version_id) if task.base_version_id else None,
            "target_version_id": str(task.target_version_id) if task.target_version_id else None,
            "execution_target": task.execution_target,
            "status": task.status.value,
            "idempotency_key": idempotency_key,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
        }
        self._upsert(tasks, values)

    def get_task(self, task_id: UUID) -> Task | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(tasks).where(tasks.c.id == str(task_id)))
                .mappings()
                .first()
            )
        return self._task_from_row(row) if row is not None else None

    def find_task_by_idempotency_key(self, idempotency_key: str) -> Task | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(tasks).where(tasks.c.idempotency_key == idempotency_key))
                .mappings()
                .first()
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
                "files_json": json.dumps(changeset.files),
                "patches_json": json.dumps(changeset.patches),
                "reason": changeset.reason,
                "risk_level": changeset.risk_level,
                "idempotency_key": changeset.idempotency_key,
                "status": changeset.status.value,
                "approval_decision": changeset.approval_decision.value,
                "created_at": changeset.created_at.isoformat(),
                "updated_at": changeset.updated_at.isoformat(),
            },
        )

    def get_changeset(self, changeset_id: UUID) -> Changeset | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(changesets).where(changesets.c.id == str(changeset_id)))
                .mappings()
                .first()
            )
        if row is None:
            return None
        return Changeset(
            id=UUID(row["id"]),
            project_id=UUID(row["project_id"]),
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            version_id=UUID(row["version_id"]),
            files=tuple(json.loads(row["files_json"])),
            patches=tuple(json.loads(row["patches_json"])),
            reason=row["reason"],
            risk_level=row["risk_level"],
            idempotency_key=row["idempotency_key"],
            status=ChangesetStatus(row["status"]),
            approval_decision=ApprovalDecision(row["approval_decision"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def find_changeset_by_idempotency_key(self, idempotency_key: str) -> Changeset | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(changesets).where(changesets.c.idempotency_key == idempotency_key)
                )
                .mappings()
                .first()
            )
        return self.get_changeset(UUID(row["id"])) if row is not None else None

    def changesets_for_task(self, task_id: UUID) -> list[Changeset]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(changesets.c.id)
                    .where(changesets.c.task_id == str(task_id))
                    .order_by(changesets.c.created_at, changesets.c.id)
                )
                .mappings()
                .all()
            )
        return [
            changeset
            for row in rows
            if (changeset := self.get_changeset(UUID(row["id"]))) is not None
        ]

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
                "created_at": approval.created_at.isoformat(),
                "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
            },
        )

    def get_approval(self, approval_id: UUID) -> Approval | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(approvals).where(approvals.c.id == str(approval_id)))
                .mappings()
                .first()
            )
        if row is None:
            return None
        return Approval(
            id=UUID(row["id"]),
            task_id=UUID(row["task_id"]),
            command_run_id=UUID(row["command_run_id"]),
            changeset_id=_uuid(row["changeset_id"]),
            requested_by=row["requested_by"],
            reason=row["reason"],
            decision=ApprovalDecision(row["decision"]),
            decided_by=row["decided_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
            decided_at=(datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None),
        )

    def find_approval_by_changeset_id(self, changeset_id: UUID) -> Approval | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(approvals.c.id).where(approvals.c.changeset_id == str(changeset_id))
                )
                .mappings()
                .first()
            )
        return self.get_approval(UUID(row["id"])) if row is not None else None

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        self._upsert(
            checkpoints,
            {
                "id": str(checkpoint.id),
                "task_id": str(checkpoint.task_id),
                "version_id": str(checkpoint.version_id),
                "changed_files_json": json.dumps(checkpoint.changed_files),
                "command_run_ids_json": json.dumps(
                    [str(run_id) for run_id in checkpoint.command_run_ids]
                ),
                "preview_artifact_id": (
                    str(checkpoint.preview_artifact_id) if checkpoint.preview_artifact_id else None
                ),
                "created_at": checkpoint.created_at.isoformat(),
            },
        )

    def get_checkpoint(self, checkpoint_id: UUID) -> Checkpoint | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(checkpoints).where(checkpoints.c.id == str(checkpoint_id))
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return Checkpoint(
            id=UUID(row["id"]),
            task_id=UUID(row["task_id"]),
            version_id=UUID(row["version_id"]),
            changed_files=tuple(json.loads(row["changed_files_json"])),
            command_run_ids=tuple(UUID(value) for value in json.loads(row["command_run_ids_json"])),
            preview_artifact_id=_uuid(row["preview_artifact_id"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def accept_version(
        self,
        *,
        project_id: UUID,
        version_id: UUID,
        expected_revision: int,
    ) -> Project:
        with self._engine.begin() as connection:
            version_row = connection.execute(
                select(versions.c.project_id).where(versions.c.id == str(version_id))
            ).first()
            if version_row is None or version_row.project_id != str(project_id):
                raise KeyError(f"version does not belong to project: {version_id}")
            current = (
                connection.execute(select(projects).where(projects.c.id == str(project_id)))
                .mappings()
                .first()
            )
            if current is None:
                raise KeyError(f"project not found: {project_id}")
            updated_at = datetime.now(UTC).isoformat()
            result = connection.execute(
                update(projects)
                .where(projects.c.id == str(project_id), projects.c.revision == expected_revision)
                .values(
                    active_version_id=str(version_id),
                    revision=expected_revision + 1,
                    updated_at=updated_at,
                )
            )
            if result.rowcount != 1:
                raise VersionConflictError(
                    f"expected project revision {expected_revision}, current revision has changed"
                )
        accepted = self.get_project(project_id)
        assert accepted is not None
        return accepted

    def _upsert(self, table: Table, values: dict[str, object]) -> None:
        statement = sqlite_insert(table).values(**values)
        updates = {key: value for key, value in values.items() if key != "id"}
        statement = statement.on_conflict_do_update(index_elements=[table.c.id], set_=updates)
        with self._engine.begin() as connection:
            connection.execute(statement)

    @staticmethod
    def _task_from_row(row: object) -> Task:
        mapping = row
        return Task(
            id=UUID(mapping["id"]),
            project_id=_uuid(mapping["project_id"]),
            conversation_id=UUID(mapping["conversation_id"]),
            user_request=mapping["user_request"],
            operation_mode=OperationMode(mapping["operation_mode"]),
            base_version_id=_uuid(mapping["base_version_id"]),
            execution_target=mapping["execution_target"],
            target_version_id=_uuid(mapping["target_version_id"]),
            status=TaskStatus(mapping["status"]),
            created_at=datetime.fromisoformat(mapping["created_at"]),
            updated_at=datetime.fromisoformat(mapping["updated_at"]),
        )
