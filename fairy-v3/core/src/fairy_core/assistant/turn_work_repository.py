from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import RowMapping

from fairy_core.assistant.work_queue import AssistantTurnWorkClaim
from fairy_core.storage.schema import assistant_turn_work, assistant_turns

_TERMINAL_STATUSES = ("completed", "cancelled", "failed")


class _TurnWorkRepositoryHost(Protocol):
    _tenant_id: str
    _session: object


class TurnWorkRepositoryMixin:
    def enqueue_turn_work(self: _TurnWorkRepositoryHost, turn_id: UUID, *, force: bool) -> int:
        now = datetime.now(UTC)
        with self._session.write() as connection:  # type: ignore[attr-defined]
            turn_status = connection.execute(
                select(assistant_turns.c.status).where(
                    assistant_turns.c.tenant_id == self._tenant_id,
                    assistant_turns.c.id == str(turn_id),
                )
            ).scalar_one_or_none()
            if turn_status is None:
                raise KeyError(f"Assistant Turn not found: {turn_id}")
            if turn_status in _TERMINAL_STATUSES:
                return 0
            statement = self._insert_work().values(  # type: ignore[attr-defined]
                tenant_id=self._tenant_id,
                turn_id=str(turn_id),
                request_revision=0,
                completed_revision=0,
                requested_at=None,
                lease_owner=None,
                lease_until=None,
                lease_fence=0,
                attempts=0,
                last_error_code=None,
                updated_at=now,
            )
            statement = statement.on_conflict_do_nothing(
                index_elements=[assistant_turn_work.c.tenant_id, assistant_turn_work.c.turn_id]
            )
            connection.execute(statement)
            row = self._work_row(connection, turn_id, for_update=True)  # type: ignore[attr-defined]
            assert row is not None
            request_revision = int(row["request_revision"])
            completed_revision = int(row["completed_revision"])
            if request_revision > completed_revision and not force:
                return request_revision
            next_revision = request_revision + 1
            connection.execute(
                update(assistant_turn_work)
                .where(
                    assistant_turn_work.c.tenant_id == self._tenant_id,
                    assistant_turn_work.c.turn_id == str(turn_id),
                    assistant_turn_work.c.request_revision == request_revision,
                )
                .values(
                    request_revision=next_revision,
                    requested_at=now,
                    last_error_code=None,
                    updated_at=now,
                )
            )
        return next_revision

    def claim_turn_work(
        self: _TurnWorkRepositoryHost,
        turn_id: UUID,
        *,
        worker_id: str,
        lease_until: datetime,
    ) -> AssistantTurnWorkClaim | None:
        return self._claim_work(  # type: ignore[attr-defined]
            turn_id=turn_id,
            worker_id=worker_id,
            lease_until=lease_until,
        )

    def claim_next_turn_work(
        self: _TurnWorkRepositoryHost,
        *,
        worker_id: str,
        lease_until: datetime,
    ) -> AssistantTurnWorkClaim | None:
        return self._claim_work(  # type: ignore[attr-defined]
            turn_id=None,
            worker_id=worker_id,
            lease_until=lease_until,
        )

    def renew_turn_work(
        self: _TurnWorkRepositoryHost,
        claim: AssistantTurnWorkClaim,
        *,
        lease_until: datetime,
    ) -> bool:
        now = datetime.now(UTC)
        normalized_until = _lease_until(lease_until, now=now)
        with self._session.write() as connection:  # type: ignore[attr-defined]
            result = connection.execute(
                update(assistant_turn_work)
                .where(
                    assistant_turn_work.c.tenant_id == self._tenant_id,
                    assistant_turn_work.c.turn_id == str(claim.turn_id),
                    assistant_turn_work.c.lease_owner == claim.lease_owner,
                    assistant_turn_work.c.lease_fence == claim.lease_fence,
                    assistant_turn_work.c.lease_until.is_not(None),
                    assistant_turn_work.c.lease_until > now,
                    assistant_turn_work.c.request_revision
                    > assistant_turn_work.c.completed_revision,
                )
                .values(lease_until=normalized_until, updated_at=now)
            )
        return result.rowcount == 1

    def release_turn_work(
        self: _TurnWorkRepositoryHost,
        claim: AssistantTurnWorkClaim,
        *,
        error_code: str | None,
    ) -> bool:
        now = datetime.now(UTC)
        with self._session.write() as connection:  # type: ignore[attr-defined]
            row = self._work_row(connection, claim.turn_id, for_update=True)  # type: ignore[attr-defined]
            if (
                row is None
                or row["lease_owner"] != claim.lease_owner
                or int(row["lease_fence"]) != claim.lease_fence
            ):
                return False
            request_revision = int(row["request_revision"])
            completed_revision = max(
                int(row["completed_revision"]),
                claim.request_revision,
            )
            still_pending = request_revision > completed_revision
            connection.execute(
                update(assistant_turn_work)
                .where(
                    assistant_turn_work.c.tenant_id == self._tenant_id,
                    assistant_turn_work.c.turn_id == str(claim.turn_id),
                    assistant_turn_work.c.lease_owner == claim.lease_owner,
                    assistant_turn_work.c.lease_fence == claim.lease_fence,
                )
                .values(
                    completed_revision=completed_revision,
                    requested_at=row["requested_at"] if still_pending else None,
                    lease_owner=None,
                    lease_until=None,
                    last_error_code=error_code,
                    updated_at=now,
                )
            )
        return True

    def abandon_turn_work(
        self: _TurnWorkRepositoryHost,
        claim: AssistantTurnWorkClaim,
    ) -> bool:
        now = datetime.now(UTC)
        with self._session.write() as connection:  # type: ignore[attr-defined]
            result = connection.execute(
                update(assistant_turn_work)
                .where(
                    assistant_turn_work.c.tenant_id == self._tenant_id,
                    assistant_turn_work.c.turn_id == str(claim.turn_id),
                    assistant_turn_work.c.lease_owner == claim.lease_owner,
                    assistant_turn_work.c.lease_fence == claim.lease_fence,
                    assistant_turn_work.c.request_revision
                    > assistant_turn_work.c.completed_revision,
                )
                .values(
                    lease_owner=None,
                    lease_until=None,
                    lease_fence=claim.lease_fence + 1,
                    last_error_code="WORKER_INTERRUPTED",
                    updated_at=now,
                )
            )
        return result.rowcount == 1

    def cancel_turn_work(self: _TurnWorkRepositoryHost, turn_id: UUID) -> bool:
        now = datetime.now(UTC)
        with self._session.write() as connection:  # type: ignore[attr-defined]
            row = self._work_row(connection, turn_id, for_update=True)  # type: ignore[attr-defined]
            if row is None:
                return False
            connection.execute(
                update(assistant_turn_work)
                .where(
                    assistant_turn_work.c.tenant_id == self._tenant_id,
                    assistant_turn_work.c.turn_id == str(turn_id),
                    assistant_turn_work.c.lease_fence == int(row["lease_fence"]),
                )
                .values(
                    completed_revision=int(row["request_revision"]),
                    requested_at=None,
                    lease_owner=None,
                    lease_until=None,
                    lease_fence=int(row["lease_fence"]) + 1,
                    updated_at=now,
                )
            )
        return True

    def pending_turn_work_ids(self: _TurnWorkRepositoryHost) -> tuple[UUID, ...]:
        with self._session.read() as connection:  # type: ignore[attr-defined]
            rows = connection.execute(
                select(assistant_turn_work.c.turn_id)
                .join(
                    assistant_turns,
                    and_(
                        assistant_turns.c.tenant_id == assistant_turn_work.c.tenant_id,
                        assistant_turns.c.id == assistant_turn_work.c.turn_id,
                    ),
                )
                .where(
                    assistant_turn_work.c.tenant_id == self._tenant_id,
                    assistant_turn_work.c.request_revision
                    > assistant_turn_work.c.completed_revision,
                    assistant_turns.c.status.not_in(_TERMINAL_STATUSES),
                    assistant_turns.c.workflow_run_id.is_(None),
                )
                .order_by(assistant_turn_work.c.requested_at, assistant_turn_work.c.turn_id)
            ).all()
        return tuple(UUID(str(row[0])) for row in rows)

    def _claim_work(
        self: _TurnWorkRepositoryHost,
        *,
        turn_id: UUID | None,
        worker_id: str,
        lease_until: datetime,
    ) -> AssistantTurnWorkClaim | None:
        normalized_worker = worker_id.strip()
        if not normalized_worker:
            raise ValueError("worker_id must not be empty")
        now = datetime.now(UTC)
        normalized_until = _lease_until(lease_until, now=now)
        with self._session.write() as connection:  # type: ignore[attr-defined]
            statement = (
                select(assistant_turn_work)
                .join(
                    assistant_turns,
                    and_(
                        assistant_turns.c.tenant_id == assistant_turn_work.c.tenant_id,
                        assistant_turns.c.id == assistant_turn_work.c.turn_id,
                    ),
                )
                .where(
                    assistant_turn_work.c.tenant_id == self._tenant_id,
                    assistant_turn_work.c.request_revision
                    > assistant_turn_work.c.completed_revision,
                    assistant_turns.c.status.not_in(_TERMINAL_STATUSES),
                    assistant_turns.c.workflow_run_id.is_(None),
                    or_(
                        assistant_turn_work.c.lease_until.is_(None),
                        assistant_turn_work.c.lease_until <= now,
                    ),
                )
                .order_by(assistant_turn_work.c.requested_at, assistant_turn_work.c.turn_id)
                .limit(1)
            )
            if turn_id is not None:
                statement = statement.where(assistant_turn_work.c.turn_id == str(turn_id))
            if self._session.dialect_name == "postgresql":  # type: ignore[attr-defined]
                statement = statement.with_for_update(
                    of=assistant_turn_work,
                    skip_locked=True,
                )
            row = connection.execute(statement).mappings().first()
            if row is None:
                return None
            previous_fence = int(row["lease_fence"])
            attempts = int(row["attempts"]) + 1
            result = connection.execute(
                update(assistant_turn_work)
                .where(
                    assistant_turn_work.c.tenant_id == self._tenant_id,
                    assistant_turn_work.c.turn_id == row["turn_id"],
                    assistant_turn_work.c.request_revision == int(row["request_revision"]),
                    assistant_turn_work.c.completed_revision == int(row["completed_revision"]),
                    assistant_turn_work.c.lease_fence == previous_fence,
                    or_(
                        assistant_turn_work.c.lease_until.is_(None),
                        assistant_turn_work.c.lease_until <= now,
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
        return AssistantTurnWorkClaim(
            turn_id=UUID(str(row["turn_id"])),
            request_revision=int(row["request_revision"]),
            lease_owner=normalized_worker,
            lease_until=normalized_until,
            lease_fence=previous_fence + 1,
            attempts=attempts,
        )

    def _work_row(
        self: _TurnWorkRepositoryHost,
        connection,
        turn_id: UUID,
        *,
        for_update: bool,
    ) -> RowMapping | None:
        statement = select(assistant_turn_work).where(
            assistant_turn_work.c.tenant_id == self._tenant_id,
            assistant_turn_work.c.turn_id == str(turn_id),
        )
        if for_update and self._session.dialect_name == "postgresql":  # type: ignore[attr-defined]
            statement = statement.with_for_update()
        return connection.execute(statement).mappings().first()

    def _insert_work(self: _TurnWorkRepositoryHost):
        dialect_name = self._session.dialect_name  # type: ignore[attr-defined]
        if dialect_name == "postgresql":
            return postgresql_insert(assistant_turn_work)
        if dialect_name == "sqlite":
            return sqlite_insert(assistant_turn_work)
        return insert(assistant_turn_work)


def _lease_until(value: datetime, *, now: datetime) -> datetime:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    normalized = normalized.astimezone(UTC)
    if normalized <= now:
        raise ValueError("lease_until must be in the future")
    return normalized


__all__ = ["TurnWorkRepositoryMixin"]
