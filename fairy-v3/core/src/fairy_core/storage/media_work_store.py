from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from fairy_core.media.work_queue import MediaWorkClaim
from fairy_core.storage.schema import media_generation_jobs, media_generation_work

_TERMINAL_STATUSES = ("completed", "failed", "cancelled", "interrupted")


class _MediaWorkStoreHost(Protocol):
    _tenant_id: str
    _session: object


class MediaWorkStoreMixin:
    def enqueue_media_work(
        self: _MediaWorkStoreHost,
        job_id: UUID,
        *,
        available_at: datetime | None = None,
    ) -> None:
        now = datetime.now(UTC)
        scheduled = _aware(available_at or now)
        with self._session.write() as connection:  # type: ignore[attr-defined]
            status = connection.execute(
                select(media_generation_jobs.c.status).where(
                    media_generation_jobs.c.tenant_id == self._tenant_id,
                    media_generation_jobs.c.id == str(job_id),
                )
            ).scalar_one_or_none()
            if status is None:
                raise KeyError(f"Media generation job not found: {job_id}")
            if status in _TERMINAL_STATUSES:
                return
            statement = self._insert_media_work().values(  # type: ignore[attr-defined]
                tenant_id=self._tenant_id,
                job_id=str(job_id),
                available_at=scheduled,
                lease_owner=None,
                lease_until=None,
                lease_fence=0,
                attempts=0,
                last_error_code=None,
                updated_at=now,
            )
            statement = statement.on_conflict_do_nothing(
                index_elements=[media_generation_work.c.tenant_id, media_generation_work.c.job_id]
            )
            connection.execute(statement)
            connection.execute(
                update(media_generation_work)
                .where(
                    media_generation_work.c.tenant_id == self._tenant_id,
                    media_generation_work.c.job_id == str(job_id),
                    media_generation_work.c.available_at.is_(None),
                    media_generation_work.c.lease_owner.is_(None),
                )
                .values(available_at=scheduled, last_error_code=None, updated_at=now)
            )

    def claim_next_media_work(
        self: _MediaWorkStoreHost,
        *,
        worker_id: str,
        lease_until: datetime,
    ) -> MediaWorkClaim | None:
        normalized_worker = worker_id.strip()
        if not normalized_worker:
            raise ValueError("worker_id must not be empty")
        now = datetime.now(UTC)
        normalized_until = _future(lease_until, now=now)
        with self._session.write() as connection:  # type: ignore[attr-defined]
            statement = (
                select(media_generation_work)
                .join(
                    media_generation_jobs,
                    and_(
                        media_generation_jobs.c.tenant_id == media_generation_work.c.tenant_id,
                        media_generation_jobs.c.id == media_generation_work.c.job_id,
                    ),
                )
                .where(
                    media_generation_work.c.tenant_id == self._tenant_id,
                    media_generation_work.c.available_at.is_not(None),
                    media_generation_work.c.available_at <= now,
                    media_generation_jobs.c.status.not_in(_TERMINAL_STATUSES),
                    or_(
                        media_generation_work.c.lease_until.is_(None),
                        media_generation_work.c.lease_until <= now,
                    ),
                )
                .order_by(media_generation_work.c.available_at, media_generation_work.c.job_id)
                .limit(1)
            )
            if self._session.dialect_name == "postgresql":  # type: ignore[attr-defined]
                statement = statement.with_for_update(
                    of=media_generation_work,
                    skip_locked=True,
                )
            row = connection.execute(statement).mappings().first()
            if row is None:
                return None
            previous_fence = int(row["lease_fence"])
            attempts = int(row["attempts"]) + 1
            result = connection.execute(
                update(media_generation_work)
                .where(
                    media_generation_work.c.tenant_id == self._tenant_id,
                    media_generation_work.c.job_id == row["job_id"],
                    media_generation_work.c.lease_fence == previous_fence,
                    media_generation_work.c.available_at.is_not(None),
                    media_generation_work.c.available_at <= now,
                    or_(
                        media_generation_work.c.lease_until.is_(None),
                        media_generation_work.c.lease_until <= now,
                    ),
                )
                .values(
                    lease_owner=normalized_worker,
                    lease_until=normalized_until,
                    lease_fence=previous_fence + 1,
                    attempts=attempts,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                return None
        return MediaWorkClaim(
            job_id=UUID(str(row["job_id"])),
            lease_owner=normalized_worker,
            lease_fence=previous_fence + 1,
            attempts=attempts,
        )

    def renew_media_work(
        self: _MediaWorkStoreHost,
        claim: MediaWorkClaim,
        *,
        lease_until: datetime,
    ) -> bool:
        now = datetime.now(UTC)
        normalized_until = _future(lease_until, now=now)
        with self._session.write() as connection:  # type: ignore[attr-defined]
            result = connection.execute(
                update(media_generation_work)
                .where(
                    media_generation_work.c.tenant_id == self._tenant_id,
                    media_generation_work.c.job_id == str(claim.job_id),
                    media_generation_work.c.lease_owner == claim.lease_owner,
                    media_generation_work.c.lease_fence == claim.lease_fence,
                    media_generation_work.c.lease_until.is_not(None),
                    media_generation_work.c.lease_until > now,
                    media_generation_work.c.available_at.is_not(None),
                )
                .values(lease_until=normalized_until, updated_at=now)
            )
        return result.rowcount == 1

    def settle_media_work(
        self: _MediaWorkStoreHost,
        claim: MediaWorkClaim,
        *,
        available_at: datetime | None,
        error_code: str | None,
    ) -> bool:
        now = datetime.now(UTC)
        scheduled = _aware(available_at) if available_at is not None else None
        with self._session.write() as connection:  # type: ignore[attr-defined]
            result = connection.execute(
                update(media_generation_work)
                .where(
                    media_generation_work.c.tenant_id == self._tenant_id,
                    media_generation_work.c.job_id == str(claim.job_id),
                    media_generation_work.c.lease_owner == claim.lease_owner,
                    media_generation_work.c.lease_fence == claim.lease_fence,
                )
                .values(
                    available_at=scheduled,
                    lease_owner=None,
                    lease_until=None,
                    last_error_code=_error_code(error_code),
                    updated_at=now,
                )
            )
        return result.rowcount == 1

    def abandon_media_work(
        self: _MediaWorkStoreHost,
        claim: MediaWorkClaim,
    ) -> bool:
        now = datetime.now(UTC)
        with self._session.write() as connection:  # type: ignore[attr-defined]
            result = connection.execute(
                update(media_generation_work)
                .where(
                    media_generation_work.c.tenant_id == self._tenant_id,
                    media_generation_work.c.job_id == str(claim.job_id),
                    media_generation_work.c.lease_owner == claim.lease_owner,
                    media_generation_work.c.lease_fence == claim.lease_fence,
                    media_generation_work.c.available_at.is_not(None),
                )
                .values(
                    available_at=now,
                    lease_owner=None,
                    lease_until=None,
                    lease_fence=claim.lease_fence + 1,
                    last_error_code="WORKER_INTERRUPTED",
                    updated_at=now,
                )
            )
        return result.rowcount == 1

    def cancel_media_work(self: _MediaWorkStoreHost, job_id: UUID) -> bool:
        now = datetime.now(UTC)
        with self._session.write() as connection:  # type: ignore[attr-defined]
            result = connection.execute(
                update(media_generation_work)
                .where(
                    media_generation_work.c.tenant_id == self._tenant_id,
                    media_generation_work.c.job_id == str(job_id),
                )
                .values(
                    available_at=None,
                    lease_owner=None,
                    lease_until=None,
                    lease_fence=media_generation_work.c.lease_fence + 1,
                    updated_at=now,
                )
            )
        return result.rowcount == 1

    def pending_media_work_ids(self: _MediaWorkStoreHost) -> tuple[UUID, ...]:
        with self._session.read() as connection:  # type: ignore[attr-defined]
            rows = connection.execute(
                select(media_generation_work.c.job_id)
                .join(
                    media_generation_jobs,
                    and_(
                        media_generation_jobs.c.tenant_id == media_generation_work.c.tenant_id,
                        media_generation_jobs.c.id == media_generation_work.c.job_id,
                    ),
                )
                .where(
                    media_generation_work.c.tenant_id == self._tenant_id,
                    media_generation_work.c.available_at.is_not(None),
                    media_generation_jobs.c.status.not_in(_TERMINAL_STATUSES),
                )
                .order_by(media_generation_work.c.available_at, media_generation_work.c.job_id)
            ).all()
        return tuple(UUID(str(row[0])) for row in rows)

    def _insert_media_work(self: _MediaWorkStoreHost):
        dialect_name = self._session.dialect_name  # type: ignore[attr-defined]
        if dialect_name == "postgresql":
            return postgresql_insert(media_generation_work)
        if dialect_name == "sqlite":
            return sqlite_insert(media_generation_work)
        return insert(media_generation_work)


def _aware(value: datetime) -> datetime:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC)


def _future(value: datetime, *, now: datetime) -> datetime:
    normalized = _aware(value)
    if normalized <= now:
        raise ValueError("lease_until must be in the future")
    return normalized


def _error_code(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized[:128] or None


__all__ = ["MediaWorkStoreMixin"]
