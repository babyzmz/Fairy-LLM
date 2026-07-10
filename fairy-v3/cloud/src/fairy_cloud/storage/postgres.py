from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fairy_core.domain.errors import VersionConflictError
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql import Select, Update
from sqlalchemy.sql.dml import Insert

from fairy_cloud.storage.schema import cloud_metadata as cloud_metadata
from fairy_cloud.storage.schema import (
    cloud_projects,
    domain_events,
    outbox,
    version_candidates,
    worker_leases,
)
from fairy_cloud.sync.models import ProjectRevisionState, SyncedEvent


def build_claim_outbox_statement(*, batch_size: int) -> Select:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    return (
        select(outbox)
        .where(
            outbox.c.published_at.is_(None),
            outbox.c.available_at <= func.now(),
            or_(outbox.c.lease_expires_at.is_(None), outbox.c.lease_expires_at <= func.now()),
        )
        .order_by(outbox.c.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )


def build_append_event_statement(
    *,
    event_id: str,
    user_id: str,
    device_id: str,
    project_id: str | None,
    schema_version: int,
    event_type: str,
    payload: Mapping[str, Any],
    conversation_id: str | None = None,
    task_id: str | None = None,
    version_id: str | None = None,
    task_sequence: int | None = None,
    visibility: str = "user",
) -> Insert:
    if schema_version < 1:
        raise ValueError("schema_version must be positive")
    return (
        postgres_insert(domain_events)
        .values(
            event_id=event_id,
            user_id=user_id,
            device_id=device_id,
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            version_id=version_id,
            task_sequence=task_sequence,
            schema_version=schema_version,
            event_type=event_type,
            visibility=visibility,
            payload=dict(payload),
        )
        .on_conflict_do_nothing(index_elements=[domain_events.c.event_id])
        .returning(domain_events.c.cursor)
    )


def build_acquire_worker_lease_statement(
    *,
    resource_type: str,
    resource_id: str,
    owner_id: str,
    now: datetime,
    expires_at: datetime,
    metadata: Mapping[str, Any],
) -> Insert:
    if expires_at <= now:
        raise ValueError("lease expiry must be in the future")
    return (
        postgres_insert(worker_leases)
        .values(
            resource_type=resource_type,
            resource_id=resource_id,
            owner_id=owner_id,
            fence=1,
            expires_at=expires_at,
            metadata=dict(metadata),
        )
        .on_conflict_do_update(
            index_elements=[worker_leases.c.resource_type, worker_leases.c.resource_id],
            set_={
                "owner_id": owner_id,
                "fence": worker_leases.c.fence + 1,
                "expires_at": expires_at,
                "metadata": dict(metadata),
            },
            where=or_(
                worker_leases.c.expires_at <= now,
                worker_leases.c.owner_id == owner_id,
            ),
        )
        .returning(worker_leases.c.fence, worker_leases.c.expires_at)
    )


def build_promote_version_statement(
    *, project_id: str, version_id: str, expected_revision: int
) -> Update:
    if expected_revision < 0:
        raise ValueError("expected_revision cannot be negative")
    return (
        update(cloud_projects)
        .where(
            cloud_projects.c.project_id == project_id,
            cloud_projects.c.revision == expected_revision,
        )
        .values(
            active_version_id=version_id,
            revision=cloud_projects.c.revision + 1,
            updated_at=func.now(),
        )
        .returning(cloud_projects.c.project_id, cloud_projects.c.revision)
    )


async def _append_event_in_transaction(
    connection: AsyncConnection,
    *,
    event_id: str,
    user_id: str,
    device_id: str,
    project_id: str | None,
    conversation_id: str | None,
    task_id: str | None,
    version_id: str | None,
    task_sequence: int | None,
    schema_version: int,
    event_type: str,
    visibility: str,
    payload: Mapping[str, Any],
) -> int:
    event_payload = dict(payload)
    result = await connection.execute(
        build_append_event_statement(
            event_id=event_id,
            user_id=user_id,
            device_id=device_id,
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            version_id=version_id,
            task_sequence=task_sequence,
            schema_version=schema_version,
            event_type=event_type,
            visibility=visibility,
            payload=event_payload,
        )
    )
    cursor = result.scalar_one_or_none()
    if cursor is None:
        existing = (
            (
                await connection.execute(
                    select(domain_events).where(domain_events.c.event_id == event_id)
                )
            )
            .mappings()
            .one()
        )
        if existing["user_id"] != user_id:
            raise PermissionError("event id belongs to a different user")
        cursor = existing["cursor"]
        device_id = str(existing["device_id"])
        project_id = existing["project_id"]
        conversation_id = existing["conversation_id"]
        task_id = existing["task_id"]
        version_id = existing["version_id"]
        task_sequence = existing["task_sequence"]
        schema_version = int(existing["schema_version"])
        event_type = str(existing["event_type"])
        visibility = str(existing["visibility"])
        event_payload = dict(existing["payload"])

    envelope = {
        "cursor": int(cursor),
        "event_id": event_id,
        "user_id": user_id,
        "device_id": device_id,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "task_id": task_id,
        "version_id": version_id,
        "task_sequence": task_sequence,
        "schema_version": schema_version,
        "event_type": event_type,
        "visibility": visibility,
        "payload": event_payload,
    }
    await connection.execute(
        postgres_insert(outbox)
        .values(event_id=event_id, topic="domain.events", payload=envelope)
        .on_conflict_do_nothing(index_elements=[outbox.c.event_id])
    )
    return int(cursor)


@dataclass(frozen=True, slots=True)
class OutboxItem:
    id: int
    event_id: str
    topic: str
    payload: dict[str, Any]
    attempts: int
    lease_owner: str
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class WorkerLease:
    resource_type: str
    resource_id: str
    owner_id: str
    fence: int
    expires_at: datetime


class LeaseUnavailableError(RuntimeError):
    """Raised when another healthy worker owns the requested lease."""


class PostgresSyncStore:
    """PostgreSQL event, version, outbox, and worker-lease adapter."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def register_project(
        self,
        *,
        project_id: str,
        user_id: str,
        active_version_id: str | None = None,
    ) -> ProjectRevisionState:
        statement = (
            postgres_insert(cloud_projects)
            .values(
                project_id=project_id,
                user_id=user_id,
                revision=0,
                active_version_id=active_version_id,
            )
            .on_conflict_do_nothing(index_elements=[cloud_projects.c.project_id])
        )
        async with self._engine.begin() as connection:
            await connection.execute(statement)
            row = (
                (
                    await connection.execute(
                        select(
                            cloud_projects.c.user_id,
                            cloud_projects.c.revision,
                            cloud_projects.c.active_version_id,
                        ).where(cloud_projects.c.project_id == project_id)
                    )
                )
                .mappings()
                .one()
            )
            if row["user_id"] != user_id:
                raise PermissionError("project belongs to a different user")

        return ProjectRevisionState(
            project_id=project_id,
            revision=int(row["revision"]),
            active_version_id=row["active_version_id"],
        )

    async def append_event(
        self,
        *,
        event_id: str,
        user_id: str,
        device_id: str,
        project_id: str | None,
        conversation_id: str | None = None,
        task_id: str | None = None,
        version_id: str | None = None,
        task_sequence: int | None = None,
        schema_version: int,
        event_type: str,
        visibility: str = "user",
        payload: Mapping[str, Any],
    ) -> int:
        async with self._engine.begin() as connection:
            return await _append_event_in_transaction(
                connection,
                event_id=event_id,
                user_id=user_id,
                device_id=device_id,
                project_id=project_id,
                conversation_id=conversation_id,
                task_id=task_id,
                version_id=version_id,
                task_sequence=task_sequence,
                schema_version=schema_version,
                event_type=event_type,
                visibility=visibility,
                payload=payload,
            )

    async def events_after(
        self,
        *,
        user_id: str,
        cursor: int,
        limit: int = 500,
        visibilities: frozenset[str] = frozenset({"user", "developer"}),
    ) -> list[SyncedEvent]:
        if cursor < 0:
            raise ValueError("cursor cannot be negative")
        if not 1 <= limit <= 2_000:
            raise ValueError("limit must be between 1 and 2000")
        if not visibilities:
            return []
        statement = (
            select(domain_events)
            .where(
                domain_events.c.user_id == user_id,
                domain_events.c.cursor > cursor,
                domain_events.c.visibility.in_(visibilities),
            )
            .order_by(domain_events.c.cursor)
            .limit(limit)
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [
            SyncedEvent(
                cursor=int(row["cursor"]),
                event_id=str(row["event_id"]),
                user_id=str(row["user_id"]),
                device_id=str(row["device_id"]),
                project_id=str(row["project_id"]) if row["project_id"] is not None else None,
                conversation_id=(
                    str(row["conversation_id"]) if row["conversation_id"] is not None else None
                ),
                task_id=str(row["task_id"]) if row["task_id"] is not None else None,
                version_id=str(row["version_id"]) if row["version_id"] is not None else None,
                task_sequence=(
                    int(row["task_sequence"]) if row["task_sequence"] is not None else None
                ),
                schema_version=int(row["schema_version"]),
                event_type=str(row["event_type"]),
                visibility=str(row["visibility"]),
                payload=dict(row["payload"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def promote_version(
        self,
        *,
        user_id: str,
        project_id: str,
        version_id: str,
        expected_revision: int,
        manifest: Mapping[str, Any],
        decision_event_id: str | None = None,
        device_id: str | None = None,
        conversation_id: str | None = None,
        task_id: str | None = None,
        task_sequence: int | None = None,
    ) -> ProjectRevisionState:
        conflict_revision: int | None = None
        promoted_revision: int | None = None
        async with self._engine.begin() as connection:
            result = await connection.execute(
                build_promote_version_statement(
                    project_id=project_id,
                    version_id=version_id,
                    expected_revision=expected_revision,
                ).where(cloud_projects.c.user_id == user_id)
            )
            promoted = result.mappings().first()
            if promoted is None:
                current = (
                    (
                        await connection.execute(
                            select(
                                cloud_projects.c.revision,
                                cloud_projects.c.active_version_id,
                            ).where(
                                cloud_projects.c.project_id == project_id,
                                cloud_projects.c.user_id == user_id,
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
                if current is None:
                    raise KeyError(f"project not found: {project_id}")
                current_revision = int(current["revision"])
                if (
                    current["active_version_id"] == version_id
                    and current_revision == expected_revision + 1
                ):
                    promoted_revision = current_revision
                else:
                    conflict_revision = current_revision
                    await connection.execute(
                        postgres_insert(version_candidates)
                        .values(
                            project_id=project_id,
                            version_id=version_id,
                            base_revision=expected_revision,
                            state="candidate",
                            manifest=dict(manifest),
                        )
                        .on_conflict_do_nothing(
                            index_elements=[
                                version_candidates.c.project_id,
                                version_candidates.c.version_id,
                            ]
                        )
                    )
            else:
                promoted_revision = int(promoted["revision"])
                await connection.execute(
                    update(version_candidates)
                    .where(
                        version_candidates.c.project_id == project_id,
                        version_candidates.c.version_id == version_id,
                    )
                    .values(state="accepted")
                )

            if decision_event_id is not None:
                if not all((device_id, conversation_id, task_id, task_sequence)):
                    raise ValueError("version decision event requires complete scope")
                outcome = "candidate_retained" if conflict_revision is not None else "promoted"
                event_type = f"version.{outcome}"
                current_revision = (
                    conflict_revision if conflict_revision is not None else promoted_revision
                )
                created_at = datetime.now(UTC)
                event_payload = {
                    "id": decision_event_id,
                    "run_id": None,
                    "project_id": project_id,
                    "conversation_id": conversation_id,
                    "task_id": task_id,
                    "version_id": version_id,
                    "task_sequence": task_sequence,
                    "event_type": event_type,
                    "visibility": "user",
                    "message": (
                        "Version retained as a conflict candidate"
                        if conflict_revision is not None
                        else "Version promoted"
                    ),
                    "payload": {
                        "expected_revision": expected_revision,
                        "project_revision": current_revision,
                        "manifest": dict(manifest),
                    },
                    "schema_version": 1,
                    "created_at": created_at.isoformat(),
                }
                await _append_event_in_transaction(
                    connection,
                    event_id=decision_event_id,
                    user_id=user_id,
                    device_id=str(device_id),
                    project_id=project_id,
                    conversation_id=str(conversation_id),
                    task_id=str(task_id),
                    version_id=version_id,
                    task_sequence=int(task_sequence),
                    schema_version=1,
                    event_type=event_type,
                    visibility="user",
                    payload=event_payload,
                )

        if conflict_revision is not None:
            raise VersionConflictError(
                f"expected project revision {expected_revision}, "
                f"current revision is {conflict_revision}; version retained as candidate"
            )
        if promoted_revision is None:
            raise RuntimeError("version promotion completed without a revision")
        return ProjectRevisionState(
            project_id=project_id,
            revision=int(promoted_revision),
            active_version_id=version_id,
        )

    async def claim_outbox(
        self,
        *,
        owner_id: str,
        batch_size: int = 100,
        lease_seconds: int = 30,
    ) -> list[OutboxItem]:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        lease_expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        async with self._engine.begin() as connection:
            rows = (
                (await connection.execute(build_claim_outbox_statement(batch_size=batch_size)))
                .mappings()
                .all()
            )
            ids = [int(row["id"]) for row in rows]
            if ids:
                await connection.execute(
                    update(outbox)
                    .where(outbox.c.id.in_(ids))
                    .values(
                        lease_owner=owner_id,
                        lease_expires_at=lease_expires_at,
                        attempts=outbox.c.attempts + 1,
                    )
                )
        return [
            OutboxItem(
                id=int(row["id"]),
                event_id=str(row["event_id"]),
                topic=str(row["topic"]),
                payload=dict(row["payload"]),
                attempts=int(row["attempts"]) + 1,
                lease_owner=owner_id,
                lease_expires_at=lease_expires_at,
            )
            for row in rows
        ]

    async def mark_outbox_published(self, *, owner_id: str, item_ids: Sequence[int]) -> int:
        if not item_ids:
            return 0
        statement = (
            update(outbox)
            .where(outbox.c.id.in_(item_ids), outbox.c.lease_owner == owner_id)
            .values(
                published_at=func.now(),
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        async with self._engine.begin() as connection:
            result = await connection.execute(statement)
        return int(result.rowcount or 0)

    async def acquire_worker_lease(
        self,
        *,
        resource_type: str,
        resource_id: str,
        owner_id: str,
        ttl_seconds: int = 30,
        metadata: Mapping[str, Any] | None = None,
    ) -> WorkerLease:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl_seconds)
        async with self._engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        build_acquire_worker_lease_statement(
                            resource_type=resource_type,
                            resource_id=resource_id,
                            owner_id=owner_id,
                            now=now,
                            expires_at=expires_at,
                            metadata=metadata or {},
                        )
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise LeaseUnavailableError(
                f"lease is held by another worker: {resource_type}/{resource_id}"
            )
        return WorkerLease(
            resource_type=resource_type,
            resource_id=resource_id,
            owner_id=owner_id,
            fence=int(row["fence"]),
            expires_at=row["expires_at"],
        )

    async def release_worker_lease(self, lease: WorkerLease) -> bool:
        statement = (
            update(worker_leases)
            .where(
                worker_leases.c.resource_type == lease.resource_type,
                worker_leases.c.resource_id == lease.resource_id,
                worker_leases.c.owner_id == lease.owner_id,
                worker_leases.c.fence == lease.fence,
            )
            .values(expires_at=datetime.now(UTC))
        )
        async with self._engine.begin() as connection:
            result = await connection.execute(statement)
        return bool(result.rowcount)
