from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Table, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from fairy_core.domain.errors import (
    IdempotencyConflictError,
    InvalidTransitionError,
    VersionConflictError,
)
from fairy_core.domain.execution import (
    Artifact,
    ArtifactType,
    ArtifactVisibility,
    PreviewHealth,
    PreviewSession,
    PreviewStatus,
    PreviewVisibility,
    RuntimeHealth,
    RuntimeKind,
    RuntimeSession,
    RuntimeStatus,
)
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.research.models import ResearchEvidence
from fairy_core.storage.schema import (
    artifacts,
    preview_sessions,
    research_evidence,
    runtime_sessions,
)

_TERMINAL_PREVIEW_STATUSES = (
    PreviewStatus.STOPPED.value,
    PreviewStatus.FAILED.value,
    PreviewStatus.INTERRUPTED.value,
)


def _uuid(value: str | None) -> UUID | None:
    return UUID(value) if value else None


def _datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _mutable_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _mutable_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_mutable_json(item) for item in value]
    return value


def _runtime_request_identity(runtime: RuntimeSession) -> tuple[object, ...]:
    return (
        runtime.project_id,
        runtime.workspace_id,
        runtime.conversation_id,
        runtime.task_id,
        runtime.version_id,
        runtime.project_root,
        runtime.execution_target,
        runtime.kind,
        runtime.executor,
    )


def _preview_request_identity(preview: PreviewSession) -> tuple[object, ...]:
    return (
        preview.project_id,
        preview.workspace_id,
        preview.conversation_id,
        preview.task_id,
        preview.version_id,
        preview.runtime_id,
        preview.project_root,
        preview.execution_target,
        preview.visibility,
    )


class ExecutionStateStoreMixin:
    """Runtime, Preview, and Artifact persistence for a tenant-scoped store."""

    _tenant_id: str
    _session: SqlAlchemySession

    def append_runtime(self, runtime: RuntimeSession) -> RuntimeSession:
        existing = self.find_runtime_by_idempotency_key(runtime.idempotency_key)
        if existing is not None:
            if _runtime_request_identity(existing) != _runtime_request_identity(runtime):
                raise IdempotencyConflictError(
                    "Runtime idempotency key is bound to a different request"
                )
            return existing
        if self._insert_execution_once(runtime_sessions, self._runtime_values(runtime)):
            return runtime
        existing = self.find_runtime_by_idempotency_key(runtime.idempotency_key)
        if existing is not None and _runtime_request_identity(
            existing
        ) == _runtime_request_identity(runtime):
            return existing
        raise IdempotencyConflictError("Runtime identity or idempotency key already exists")

    def save_runtime(
        self,
        runtime: RuntimeSession,
        *,
        expected_revision: int,
    ) -> RuntimeSession:
        self._validate_revision_advance(runtime.revision, expected_revision, "Runtime")
        result = self._update_execution_revisioned(
            runtime_sessions,
            self._runtime_values(runtime),
            expected_revision=expected_revision,
        )
        if result != 1:
            raise VersionConflictError(
                f"expected Runtime revision {expected_revision}, current revision has changed"
            )
        return runtime

    def get_runtime(self, runtime_id: UUID) -> RuntimeSession | None:
        row = self._get_by_id(runtime_sessions, runtime_id)
        return self._runtime_from_row(row) if row is not None else None

    def find_runtime_by_idempotency_key(self, key: str) -> RuntimeSession | None:
        row = self._first(
            select(runtime_sessions).where(
                runtime_sessions.c.tenant_id == self._tenant_id,
                runtime_sessions.c.idempotency_key == key,
            )
        )
        return self._runtime_from_row(row) if row is not None else None

    def runtimes_for_task(self, task_id: UUID) -> list[RuntimeSession]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(runtime_sessions)
                    .where(
                        runtime_sessions.c.tenant_id == self._tenant_id,
                        runtime_sessions.c.task_id == str(task_id),
                    )
                    .order_by(runtime_sessions.c.created_at, runtime_sessions.c.id)
                )
                .mappings()
                .all()
            )
        return [self._runtime_from_row(row) for row in rows]

    def recoverable_runtimes(self) -> list[RuntimeSession]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(runtime_sessions)
                    .where(
                        runtime_sessions.c.tenant_id == self._tenant_id,
                        runtime_sessions.c.status.in_(
                            (
                                RuntimeStatus.STARTING.value,
                                RuntimeStatus.RUNNING.value,
                                RuntimeStatus.STOPPING.value,
                            )
                        ),
                    )
                    .order_by(runtime_sessions.c.updated_at, runtime_sessions.c.id)
                )
                .mappings()
                .all()
            )
        return [self._runtime_from_row(row) for row in rows]

    def append_preview(self, preview: PreviewSession) -> PreviewSession:
        existing = self.find_preview_by_idempotency_key(preview.idempotency_key)
        if existing is not None:
            if _preview_request_identity(existing) != _preview_request_identity(preview):
                raise IdempotencyConflictError(
                    "Preview idempotency key is bound to a different request"
                )
            return existing
        active = self.preview_for_task(preview.task_id)
        if active is not None:
            raise InvalidTransitionError("Task already has a non-terminal Preview")
        if self._insert_execution_once(preview_sessions, self._preview_values(preview)):
            return preview
        existing = self.find_preview_by_idempotency_key(preview.idempotency_key)
        if existing is not None and _preview_request_identity(
            existing
        ) == _preview_request_identity(preview):
            return existing
        if self.preview_for_task(preview.task_id) is not None:
            raise InvalidTransitionError("Task already has a non-terminal Preview")
        raise IdempotencyConflictError("Preview identity or idempotency key already exists")

    def save_preview(
        self,
        preview: PreviewSession,
        *,
        expected_revision: int,
    ) -> PreviewSession:
        self._validate_revision_advance(preview.revision, expected_revision, "Preview")
        if preview.status.value not in _TERMINAL_PREVIEW_STATUSES:
            active = self.preview_for_task(preview.task_id)
            if active is not None and active.id != preview.id:
                raise InvalidTransitionError("Task already has a non-terminal Preview")
        values = self._preview_values(preview)
        try:
            with self._session.write() as connection, connection.begin_nested():
                result = connection.execute(
                    update(preview_sessions)
                    .where(
                        preview_sessions.c.tenant_id == self._tenant_id,
                        preview_sessions.c.id == values["id"],
                        preview_sessions.c.revision == expected_revision,
                    )
                    .values(
                        **{
                            key: value
                            for key, value in values.items()
                            if key not in {"id", "tenant_id"}
                        }
                    )
                )
        except IntegrityError as error:
            raise InvalidTransitionError("Task already has a non-terminal Preview") from error
        if result.rowcount != 1:
            raise VersionConflictError(
                f"expected Preview revision {expected_revision}, current revision has changed"
            )
        return preview

    def get_preview(self, preview_id: UUID) -> PreviewSession | None:
        row = self._get_by_id(preview_sessions, preview_id)
        return self._preview_from_row(row) if row is not None else None

    def find_preview_by_idempotency_key(self, key: str) -> PreviewSession | None:
        row = self._first(
            select(preview_sessions).where(
                preview_sessions.c.tenant_id == self._tenant_id,
                preview_sessions.c.idempotency_key == key,
            )
        )
        return self._preview_from_row(row) if row is not None else None

    def preview_for_task(
        self,
        task_id: UUID,
        *,
        include_terminal: bool = False,
    ) -> PreviewSession | None:
        if include_terminal:
            active = self.preview_for_task(task_id)
            if active is not None:
                return active
        statement = select(preview_sessions).where(
            preview_sessions.c.tenant_id == self._tenant_id,
            preview_sessions.c.task_id == str(task_id),
        )
        if not include_terminal:
            statement = statement.where(
                preview_sessions.c.status.not_in(_TERMINAL_PREVIEW_STATUSES)
            )
        row = self._first(
            statement.order_by(
                preview_sessions.c.updated_at.desc(),
                preview_sessions.c.id.desc(),
            )
        )
        return self._preview_from_row(row) if row is not None else None

    def previews_for_conversation(self, conversation_id: UUID) -> list[PreviewSession]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(preview_sessions)
                    .where(
                        preview_sessions.c.tenant_id == self._tenant_id,
                        preview_sessions.c.conversation_id == str(conversation_id),
                    )
                    .order_by(preview_sessions.c.created_at, preview_sessions.c.id)
                )
                .mappings()
                .all()
            )
        return [self._preview_from_row(row) for row in rows]

    def append_artifact(self, artifact: Artifact) -> Artifact:
        existing = self.get_artifact(artifact.id)
        if existing is not None:
            if existing != artifact:
                raise IdempotencyConflictError("Artifact identity is immutable")
            return existing
        if self._insert_execution_once(artifacts, self._artifact_values(artifact)):
            return artifact
        existing = self.get_artifact(artifact.id)
        if existing == artifact:
            return existing
        raise IdempotencyConflictError("Artifact identity is immutable")

    def get_artifact(self, artifact_id: UUID) -> Artifact | None:
        row = self._get_by_id(artifacts, artifact_id)
        return self._artifact_from_row(row) if row is not None else None

    def artifacts_for_task(self, task_id: UUID) -> list[Artifact]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(artifacts)
                    .where(
                        artifacts.c.tenant_id == self._tenant_id,
                        artifacts.c.task_id == str(task_id),
                    )
                    .order_by(artifacts.c.created_at, artifacts.c.id)
                )
                .mappings()
                .all()
            )
        return [self._artifact_from_row(row) for row in rows]

    def append_research_evidence(
        self,
        evidence: ResearchEvidence,
    ) -> ResearchEvidence:
        if self._insert_execution_once(
            research_evidence,
            self._research_evidence_values(evidence),
        ):
            return evidence
        existing = self._research_evidence_by_id(evidence.id)
        if existing == evidence:
            return existing
        raise IdempotencyConflictError("Research Evidence identity is immutable")

    def research_evidence_for_artifact(
        self,
        artifact_id: UUID,
    ) -> list[ResearchEvidence]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(research_evidence)
                    .where(
                        research_evidence.c.tenant_id == self._tenant_id,
                        research_evidence.c.artifact_id == str(artifact_id),
                    )
                    .order_by(
                        research_evidence.c.ordinal,
                        research_evidence.c.id,
                    )
                )
                .mappings()
                .all()
            )
        return [self._research_evidence_from_row(row) for row in rows]

    def _research_evidence_by_id(
        self,
        evidence_id: UUID,
    ) -> ResearchEvidence | None:
        row = self._get_by_id(research_evidence, evidence_id)
        return self._research_evidence_from_row(row) if row is not None else None

    def _insert_execution_once(self, table: Table, values: dict[str, object]) -> bool:
        scoped_values = {"tenant_id": self._tenant_id, **values}
        if self._session.dialect_name == "postgresql":
            statement = postgresql_insert(table).values(**scoped_values)
            statement = statement.on_conflict_do_nothing()
        else:
            statement = sqlite_insert(table).values(**scoped_values)
            statement = statement.on_conflict_do_nothing()
        with self._session.write() as connection:
            result = connection.execute(statement)
        return result.rowcount == 1

    def _update_execution_revisioned(
        self,
        table: Table,
        values: dict[str, object],
        *,
        expected_revision: int,
    ) -> int:
        with self._session.write() as connection:
            result = connection.execute(
                update(table)
                .where(
                    table.c.tenant_id == self._tenant_id,
                    table.c.id == values["id"],
                    table.c.revision == expected_revision,
                )
                .values(
                    **{
                        key: value
                        for key, value in values.items()
                        if key not in {"id", "tenant_id"}
                    }
                )
            )
        return result.rowcount

    @staticmethod
    def _validate_revision_advance(
        actual_revision: int,
        expected_revision: int,
        entity_name: str,
    ) -> None:
        if expected_revision < 0:
            raise ValueError("expected_revision cannot be negative")
        if actual_revision != expected_revision + 1:
            raise ValueError(
                f"{entity_name} revision must advance exactly once from expected_revision"
            )

    @staticmethod
    def _runtime_values(runtime: RuntimeSession) -> dict[str, object]:
        return {
            "id": str(runtime.id),
            "project_id": str(runtime.project_id) if runtime.project_id else None,
            "workspace_id": str(runtime.workspace_id),
            "conversation_id": str(runtime.conversation_id),
            "task_id": str(runtime.task_id),
            "version_id": str(runtime.version_id) if runtime.version_id else None,
            "project_root": str(runtime.project_root),
            "execution_target": runtime.execution_target,
            "kind": runtime.kind.value,
            "executor": runtime.executor,
            "executor_handle": runtime.executor_handle,
            "port": runtime.port,
            "status": runtime.status.value,
            "health": runtime.health.value,
            "error_code": runtime.error_code,
            "idempotency_key": runtime.idempotency_key,
            "revision": runtime.revision,
            "created_at": runtime.created_at,
            "updated_at": runtime.updated_at,
        }

    @staticmethod
    def _preview_values(preview: PreviewSession) -> dict[str, object]:
        return {
            "id": str(preview.id),
            "project_id": str(preview.project_id) if preview.project_id else None,
            "workspace_id": str(preview.workspace_id),
            "conversation_id": str(preview.conversation_id),
            "task_id": str(preview.task_id),
            "version_id": str(preview.version_id) if preview.version_id else None,
            "runtime_id": str(preview.runtime_id),
            "project_root": str(preview.project_root),
            "execution_target": preview.execution_target,
            "url": preview.url,
            "visibility": preview.visibility.value,
            "status": preview.status.value,
            "health": preview.health.value,
            "error_code": preview.error_code,
            "idempotency_key": preview.idempotency_key,
            "revision": preview.revision,
            "created_at": preview.created_at,
            "updated_at": preview.updated_at,
        }

    @staticmethod
    def _artifact_values(artifact: Artifact) -> dict[str, object]:
        return {
            "id": str(artifact.id),
            "project_id": str(artifact.project_id) if artifact.project_id else None,
            "conversation_id": str(artifact.conversation_id),
            "task_id": str(artifact.task_id),
            "version_id": str(artifact.version_id) if artifact.version_id else None,
            "artifact_type": artifact.artifact_type.value,
            "visibility": artifact.visibility.value,
            "storage_location": artifact.storage_location,
            "media_type": artifact.media_type,
            "byte_length": artifact.byte_length,
            "content_hash": artifact.content_hash,
            "metadata": _mutable_json(artifact.metadata),
            "created_at": artifact.created_at,
        }

    @staticmethod
    def _research_evidence_values(
        evidence: ResearchEvidence,
    ) -> dict[str, object]:
        return {
            "id": str(evidence.id),
            "artifact_id": str(evidence.artifact_id),
            "project_id": str(evidence.project_id) if evidence.project_id else None,
            "conversation_id": str(evidence.conversation_id),
            "task_id": str(evidence.task_id),
            "version_id": str(evidence.version_id) if evidence.version_id else None,
            "ordinal": evidence.ordinal,
            "source_url": evidence.source_url,
            "canonical_url": evidence.canonical_url,
            "redirect_chain": list(evidence.redirect_chain),
            "title": evidence.title,
            "media_type": evidence.media_type,
            "byte_length": evidence.byte_length,
            "content_hash": evidence.content_hash,
            "excerpt": evidence.excerpt,
            "fetched_at": evidence.fetched_at,
            "created_at": evidence.created_at,
        }

    @staticmethod
    def _runtime_from_row(row: Mapping[str, Any]) -> RuntimeSession:
        return RuntimeSession.restore(
            id=UUID(row["id"]),
            project_id=_uuid(row["project_id"]),
            workspace_id=UUID(row["workspace_id"]),
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            version_id=_uuid(row["version_id"]),
            project_root=Path(row["project_root"]),
            execution_target=row["execution_target"],
            kind=RuntimeKind(row["kind"]),
            executor=row["executor"],
            executor_handle=row["executor_handle"],
            port=int(row["port"]) if row["port"] is not None else None,
            status=RuntimeStatus(row["status"]),
            health=RuntimeHealth(row["health"]),
            error_code=row["error_code"],
            idempotency_key=row["idempotency_key"],
            revision=int(row["revision"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _preview_from_row(row: Mapping[str, Any]) -> PreviewSession:
        return PreviewSession.restore(
            id=UUID(row["id"]),
            project_id=_uuid(row["project_id"]),
            workspace_id=UUID(row["workspace_id"]),
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            version_id=_uuid(row["version_id"]),
            runtime_id=UUID(row["runtime_id"]),
            project_root=Path(row["project_root"]),
            execution_target=row["execution_target"],
            url=row["url"],
            visibility=PreviewVisibility(row["visibility"]),
            status=PreviewStatus(row["status"]),
            health=PreviewHealth(row["health"]),
            error_code=row["error_code"],
            idempotency_key=row["idempotency_key"],
            revision=int(row["revision"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _artifact_from_row(row: Mapping[str, Any]) -> Artifact:
        return Artifact.restore(
            id=UUID(row["id"]),
            project_id=_uuid(row["project_id"]),
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            version_id=_uuid(row["version_id"]),
            artifact_type=ArtifactType(row["artifact_type"]),
            visibility=ArtifactVisibility(row["visibility"]),
            storage_location=row["storage_location"],
            media_type=row["media_type"],
            byte_length=int(row["byte_length"]),
            content_hash=row["content_hash"],
            metadata=row["metadata"],
            created_at=_datetime(row["created_at"]),
        )

    @staticmethod
    def _research_evidence_from_row(
        row: Mapping[str, Any],
    ) -> ResearchEvidence:
        return ResearchEvidence.restore(
            id=UUID(row["id"]),
            artifact_id=UUID(row["artifact_id"]),
            project_id=_uuid(row["project_id"]),
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            version_id=_uuid(row["version_id"]),
            ordinal=int(row["ordinal"]),
            source_url=row["source_url"],
            canonical_url=row["canonical_url"],
            redirect_chain=tuple(row["redirect_chain"]),
            title=row["title"],
            media_type=row["media_type"],
            byte_length=int(row["byte_length"]),
            content_hash=row["content_hash"],
            excerpt=row["excerpt"],
            fetched_at=_datetime(row["fetched_at"]),
            created_at=_datetime(row["created_at"]),
        )
