from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, update

from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.media.models import (
    MediaGenerationJob,
    MediaGenerationKind,
    MediaGenerationStatus,
)
from fairy_core.model_catalog.models import ModelEndpointKind
from fairy_core.storage.schema import media_generation_jobs

_RECOVERABLE_STATUSES = (
    MediaGenerationStatus.CREATED.value,
    MediaGenerationStatus.GENERATING.value,
    MediaGenerationStatus.PENDING.value,
    MediaGenerationStatus.IN_PROGRESS.value,
)


def _datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class MediaStateStoreMixin:
    _tenant_id: str
    _session: Any

    def save_media_job(self, job: MediaGenerationJob) -> MediaGenerationJob:
        existing = self.find_media_job_by_idempotency_key(job.idempotency_key)
        if existing is not None:
            if existing.request_fingerprint != job.request_fingerprint:
                raise IdempotencyConflictError("Media request idempotency key was reused")
            return existing
        with self._session.write() as connection:
            connection.execute(
                insert(media_generation_jobs).values(
                    tenant_id=self._tenant_id,
                    **self._media_job_values(job),
                )
            )
        return job

    def update_media_job(
        self,
        job: MediaGenerationJob,
        *,
        expected_revision: int,
        expected_status: MediaGenerationStatus,
    ) -> MediaGenerationJob:
        if job.revision != expected_revision + 1:
            raise ValueError("Media job revision must advance exactly once")
        values = self._media_job_values(job)
        with self._session.write() as connection:
            result = connection.execute(
                update(media_generation_jobs)
                .where(
                    media_generation_jobs.c.tenant_id == self._tenant_id,
                    media_generation_jobs.c.id == str(job.id),
                    media_generation_jobs.c.revision == expected_revision,
                    media_generation_jobs.c.status == expected_status.value,
                )
                .values(
                    **{
                        key: value
                        for key, value in values.items()
                        if key
                        not in {
                            "id",
                            "project_id",
                            "workspace_id",
                            "conversation_id",
                            "task_id",
                            "version_id",
                            "turn_id",
                            "command_run_id",
                            "scope_digest",
                            "kind",
                            "model_id",
                            "endpoint_kind",
                            "request_spec",
                            "request_fingerprint",
                            "idempotency_key",
                            "artifact_id",
                            "created_at",
                        }
                    }
                )
            )
        if result.rowcount != 1:
            raise VersionConflictError("Media job state changed concurrently")
        return job

    def get_media_job(self, job_id: UUID) -> MediaGenerationJob | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(media_generation_jobs).where(
                        media_generation_jobs.c.tenant_id == self._tenant_id,
                        media_generation_jobs.c.id == str(job_id),
                    )
                )
                .mappings()
                .first()
            )
        return self._media_job_from_row(row) if row is not None else None

    def find_media_job_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> MediaGenerationJob | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(media_generation_jobs).where(
                        media_generation_jobs.c.tenant_id == self._tenant_id,
                        media_generation_jobs.c.idempotency_key == idempotency_key,
                    )
                )
                .mappings()
                .first()
            )
        return self._media_job_from_row(row) if row is not None else None

    def recoverable_media_jobs(self) -> list[MediaGenerationJob]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(media_generation_jobs)
                    .where(
                        media_generation_jobs.c.tenant_id == self._tenant_id,
                        media_generation_jobs.c.status.in_(_RECOVERABLE_STATUSES),
                    )
                    .order_by(
                        media_generation_jobs.c.updated_at,
                        media_generation_jobs.c.id,
                    )
                )
                .mappings()
                .all()
            )
        return [self._media_job_from_row(row) for row in rows]

    def list_media_jobs(self, task_id: UUID) -> list[MediaGenerationJob]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(media_generation_jobs)
                    .where(
                        media_generation_jobs.c.tenant_id == self._tenant_id,
                        media_generation_jobs.c.task_id == str(task_id),
                    )
                    .order_by(
                        media_generation_jobs.c.created_at,
                        media_generation_jobs.c.id,
                    )
                )
                .mappings()
                .all()
            )
        return [self._media_job_from_row(row) for row in rows]

    @staticmethod
    def _media_job_values(job: MediaGenerationJob) -> dict[str, object]:
        return {
            "id": str(job.id),
            "project_id": str(job.project_id) if job.project_id else None,
            "workspace_id": str(job.workspace_id),
            "conversation_id": str(job.conversation_id),
            "task_id": str(job.task_id),
            "version_id": str(job.version_id),
            "turn_id": str(job.turn_id) if job.turn_id else None,
            "command_run_id": str(job.command_run_id),
            "scope_digest": job.scope_digest,
            "kind": job.kind.value,
            "model_id": job.model_id,
            "endpoint_kind": job.endpoint_kind.value,
            "output_path": job.output_path,
            "request_spec": dict(job.request_spec),
            "request_fingerprint": job.request_fingerprint,
            "idempotency_key": job.idempotency_key,
            "artifact_id": str(job.artifact_id),
            "status": job.status.value,
            "provider_job_id": job.provider_job_id,
            "progress": job.progress,
            "usage_cost": job.usage_cost,
            "error_code": job.error_code,
            "revision": job.revision,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }

    @staticmethod
    def _media_job_from_row(row: Mapping[str, Any]) -> MediaGenerationJob:
        return MediaGenerationJob(
            id=UUID(row["id"]),
            project_id=UUID(row["project_id"]) if row["project_id"] else None,
            workspace_id=UUID(row["workspace_id"]),
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            version_id=UUID(row["version_id"]),
            turn_id=UUID(row["turn_id"]) if row["turn_id"] else None,
            command_run_id=UUID(row["command_run_id"]),
            scope_digest=row["scope_digest"],
            kind=MediaGenerationKind(row["kind"]),
            model_id=row["model_id"],
            endpoint_kind=ModelEndpointKind(row["endpoint_kind"]),
            output_path=row["output_path"],
            request_spec=MappingProxyType(dict(row["request_spec"])),
            request_fingerprint=row["request_fingerprint"],
            idempotency_key=row["idempotency_key"],
            artifact_id=UUID(row["artifact_id"]),
            status=MediaGenerationStatus(row["status"]),
            provider_job_id=row["provider_job_id"],
            progress=int(row["progress"]),
            usage_cost=row["usage_cost"],
            error_code=row["error_code"],
            revision=int(row["revision"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )


__all__ = ["MediaStateStoreMixin"]
