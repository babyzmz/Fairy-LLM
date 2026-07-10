from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql import Select, Update
from sqlalchemy.sql.dml import Insert

from fairy_cloud.storage.schema import cloud_metadata as cloud_metadata
from fairy_cloud.storage.schema import (
    core_projects,
    core_tenants,
    domain_events,
    outbox,
    version_candidates,
    worker_leases,
)
from fairy_cloud.sync.fingerprints import canonical_payload_fingerprint
from fairy_cloud.sync.models import ProjectRevisionState, SyncedEvent


def tenant_id_for_user(user_id: str) -> str:
    if not user_id.strip():
        raise ValueError("user_id must not be empty")
    return hashlib.sha256(f"fairy:v3:tenant:{user_id}".encode()).hexdigest()


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


def build_events_after_statement(
    *,
    tenant_id: str,
    cursor: int,
    limit: int,
    visibilities: frozenset[str],
) -> Select:
    if cursor < 0:
        raise ValueError("cursor cannot be negative")
    if not 1 <= limit <= 2_000:
        raise ValueError("limit must be between 1 and 2000")
    if not visibilities:
        raise ValueError("visibilities must not be empty")
    return (
        select(domain_events)
        .where(
            domain_events.c.tenant_id == tenant_id,
            domain_events.c.cursor > cursor,
            domain_events.c.visibility.in_(visibilities),
        )
        .order_by(domain_events.c.cursor)
        .limit(limit)
    )


def build_append_event_statement(
    *,
    tenant_id: str,
    event_id: str,
    run_id: str | None,
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
    message: str | None = None,
) -> Insert:
    if schema_version < 1:
        raise ValueError("schema_version must be positive")
    return (
        postgres_insert(domain_events)
        .values(
            tenant_id=tenant_id,
            event_id=event_id,
            run_id=run_id,
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
            message=message or event_type,
            payload=dict(payload),
        )
        .on_conflict_do_nothing()
        .returning(domain_events.c.cursor, domain_events.c.created_at)
    )


def build_acquire_worker_lease_statement(
    *,
    tenant_id: str,
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
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            owner_id=owner_id,
            fence=1,
            expires_at=expires_at,
            metadata=dict(metadata),
        )
        .on_conflict_do_update(
            index_elements=[
                worker_leases.c.tenant_id,
                worker_leases.c.resource_type,
                worker_leases.c.resource_id,
            ],
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
    *, tenant_id: str, project_id: str, version_id: str, expected_revision: int
) -> Update:
    if expected_revision < 0:
        raise ValueError("expected_revision cannot be negative")
    return (
        update(core_projects)
        .where(
            core_projects.c.tenant_id == tenant_id,
            core_projects.c.id == project_id,
            core_projects.c.revision == expected_revision,
        )
        .values(
            active_version_id=version_id,
            revision=core_projects.c.revision + 1,
            updated_at=func.now(),
        )
        .returning(core_projects.c.id, core_projects.c.revision)
    )


async def _append_event_in_transaction(
    connection: AsyncConnection,
    *,
    tenant_id: str,
    event_id: str,
    run_id: str | None,
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
    message: str,
    payload: Mapping[str, Any],
) -> int:
    event_payload = dict(payload)
    result = await connection.execute(
        build_append_event_statement(
            tenant_id=tenant_id,
            event_id=event_id,
            run_id=run_id,
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
            message=message,
            payload=event_payload,
        )
    )
    inserted = result.mappings().one_or_none()
    if inserted is None:
        existing = (
            (
                await connection.execute(
                    select(domain_events).where(
                        domain_events.c.tenant_id == tenant_id,
                        domain_events.c.event_id == event_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        if existing is None:
            if task_id is not None and task_sequence is not None:
                sequence_event_id = (
                    await connection.execute(
                        select(domain_events.c.event_id).where(
                            domain_events.c.tenant_id == tenant_id,
                            domain_events.c.task_id == task_id,
                            domain_events.c.task_sequence == task_sequence,
                        )
                    )
                ).scalar_one_or_none()
                if sequence_event_id is not None:
                    raise IdempotencyConflictError(
                        f"task sequence {task_sequence} is already owned by event "
                        f"{sequence_event_id}"
                    )
            raise RuntimeError("event insert conflicted without an identifiable ledger row")
        immutable = {
            "run_id": run_id,
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
            "message": message,
        }
        mismatched = [
            field for field, requested in immutable.items() if existing[field] != requested
        ]
        if canonical_payload_fingerprint(existing["payload"]) != canonical_payload_fingerprint(
            event_payload
        ):
            mismatched.append("payload")
        if mismatched:
            raise IdempotencyConflictError(
                f"event replay changed immutable fields: {', '.join(mismatched)}"
            )
        cursor = existing["cursor"]
        created_at = existing["created_at"]
    else:
        cursor = inserted["cursor"]
        created_at = inserted["created_at"]

    envelope = {
        "tenant_id": tenant_id,
        "cursor": int(cursor),
        "event_id": event_id,
        "run_id": run_id,
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
        "message": message,
        "payload": event_payload,
        "created_at": _canonical_event_timestamp(created_at),
    }
    outbox_result = await connection.execute(
        postgres_insert(outbox)
        .values(
            tenant_id=tenant_id,
            event_id=event_id,
            topic="domain.events",
            payload=envelope,
        )
        .on_conflict_do_nothing(index_elements=[outbox.c.tenant_id, outbox.c.event_id])
        .returning(outbox.c.id)
    )
    if outbox_result.scalar_one_or_none() is None:
        existing_outbox = (
            (
                await connection.execute(
                    select(outbox.c.topic, outbox.c.payload).where(
                        outbox.c.tenant_id == tenant_id,
                        outbox.c.event_id == event_id,
                    )
                )
            )
            .mappings()
            .one()
        )
        if existing_outbox["topic"] != "domain.events" or canonical_payload_fingerprint(
            existing_outbox["payload"]
        ) != canonical_payload_fingerprint(envelope):
            raise IdempotencyConflictError("outbox replay changed immutable payload")
    return int(cursor)


@dataclass(frozen=True, slots=True)
class OutboxItem:
    tenant_id: str
    id: int
    event_id: str
    topic: str
    payload: dict[str, Any]
    attempts: int
    lease_owner: str
    lease_expires_at: datetime
    lease_fence: int


@dataclass(frozen=True, slots=True)
class WorkerLease:
    tenant_id: str
    resource_type: str
    resource_id: str
    owner_id: str
    fence: int
    expires_at: datetime


class LeaseUnavailableError(RuntimeError):
    """Raised when another healthy worker owns the requested lease."""


async def _set_tenant(connection: AsyncConnection, tenant_id: str) -> None:
    await connection.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )


def _canonical_event_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


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
        tenant_id = tenant_id_for_user(user_id)
        now = datetime.now(UTC)
        statement = (
            postgres_insert(core_projects)
            .values(
                tenant_id=tenant_id,
                id=project_id,
                name=f"Cloud {project_id}",
                residency="synced",
                revision=0,
                active_version_id=active_version_id,
                active_preview_id=None,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[core_projects.c.tenant_id, core_projects.c.id])
        )
        async with self._engine.begin() as connection:
            await _set_tenant(connection, tenant_id)
            await connection.execute(
                postgres_insert(core_tenants)
                .values(tenant_id=tenant_id, subject_id=user_id)
                .on_conflict_do_nothing(index_elements=[core_tenants.c.tenant_id])
            )
            await connection.execute(statement)
            row = (
                (
                    await connection.execute(
                        select(
                            core_projects.c.revision,
                            core_projects.c.active_version_id,
                        ).where(
                            core_projects.c.tenant_id == tenant_id,
                            core_projects.c.id == project_id,
                        )
                    )
                )
                .mappings()
                .one()
            )
        return ProjectRevisionState(
            project_id=project_id,
            revision=int(row["revision"]),
            active_version_id=row["active_version_id"],
        )

    async def append_event(
        self,
        *,
        event_id: str,
        run_id: str | None = None,
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
        message: str | None = None,
        payload: Mapping[str, Any],
    ) -> int:
        tenant_id = tenant_id_for_user(user_id)
        async with self._engine.begin() as connection:
            await _set_tenant(connection, tenant_id)
            return await _append_event_in_transaction(
                connection,
                tenant_id=tenant_id,
                event_id=event_id,
                run_id=run_id,
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
                message=message or event_type,
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
        tenant_id = tenant_id_for_user(user_id)
        statement = build_events_after_statement(
            tenant_id=tenant_id,
            cursor=cursor,
            limit=limit,
            visibilities=visibilities,
        )
        async with self._engine.begin() as connection:
            await _set_tenant(connection, tenant_id)
            rows = (await connection.execute(statement)).mappings().all()
        return [
            SyncedEvent(
                cursor=int(row["cursor"]),
                event_id=str(row["event_id"]),
                run_id=str(row["run_id"]) if row["run_id"] is not None else None,
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
                message=str(row["message"]),
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
        tenant_id = tenant_id_for_user(user_id)
        conflict_revision: int | None = None
        promoted_revision: int | None = None
        async with self._engine.begin() as connection:
            await _set_tenant(connection, tenant_id)
            result = await connection.execute(
                build_promote_version_statement(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    version_id=version_id,
                    expected_revision=expected_revision,
                )
            )
            promoted = result.mappings().first()
            if promoted is None:
                current = (
                    (
                        await connection.execute(
                            select(
                                core_projects.c.revision,
                                core_projects.c.active_version_id,
                            ).where(
                                core_projects.c.tenant_id == tenant_id,
                                core_projects.c.id == project_id,
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
                            tenant_id=tenant_id,
                            project_id=project_id,
                            version_id=version_id,
                            base_revision=expected_revision,
                            state="candidate",
                            manifest=dict(manifest),
                        )
                        .on_conflict_do_nothing(
                            index_elements=[
                                version_candidates.c.tenant_id,
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
                        version_candidates.c.tenant_id == tenant_id,
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
                message = (
                    "Version retained as a conflict candidate"
                    if conflict_revision is not None
                    else "Version promoted"
                )
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
                    "message": message,
                    "payload": {
                        "expected_revision": expected_revision,
                        "project_revision": current_revision,
                        "manifest": dict(manifest),
                    },
                    "schema_version": 1,
                }
                await _append_event_in_transaction(
                    connection,
                    tenant_id=tenant_id,
                    event_id=decision_event_id,
                    run_id=None,
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
                    message=message,
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
        claimed_rows: list[Mapping[str, Any]] = []
        async with self._engine.begin() as connection:
            rows = (
                (await connection.execute(build_claim_outbox_statement(batch_size=batch_size)))
                .mappings()
                .all()
            )
            for row in rows:
                claimed = (
                    (
                        await connection.execute(
                            update(outbox)
                            .where(
                                outbox.c.tenant_id == row["tenant_id"],
                                outbox.c.id == row["id"],
                                outbox.c.lease_fence == row["lease_fence"],
                            )
                            .values(
                                lease_owner=owner_id,
                                lease_expires_at=lease_expires_at,
                                attempts=outbox.c.attempts + 1,
                                lease_fence=outbox.c.lease_fence + 1,
                            )
                            .returning(outbox)
                        )
                    )
                    .mappings()
                    .one()
                )
                claimed_rows.append(claimed)
        return [
            OutboxItem(
                tenant_id=str(row["tenant_id"]),
                id=int(row["id"]),
                event_id=str(row["event_id"]),
                topic=str(row["topic"]),
                payload=dict(row["payload"]),
                attempts=int(row["attempts"]),
                lease_owner=owner_id,
                lease_expires_at=lease_expires_at,
                lease_fence=int(row["lease_fence"]),
            )
            for row in claimed_rows
        ]

    async def mark_outbox_published(
        self,
        *,
        owner_id: str,
        items: Sequence[OutboxItem],
    ) -> int:
        if not items:
            return 0
        published = 0
        async with self._engine.begin() as connection:
            for item in items:
                result = await connection.execute(
                    update(outbox)
                    .where(
                        outbox.c.tenant_id == item.tenant_id,
                        outbox.c.id == item.id,
                        outbox.c.lease_owner == owner_id,
                        outbox.c.lease_fence == item.lease_fence,
                    )
                    .values(
                        published_at=func.now(),
                        lease_owner=None,
                        lease_expires_at=None,
                    )
                )
                published += int(result.rowcount or 0)
        return published

    async def acquire_worker_lease(
        self,
        *,
        tenant_id: str,
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
            await _set_tenant(connection, tenant_id)
            row = (
                (
                    await connection.execute(
                        build_acquire_worker_lease_statement(
                            tenant_id=tenant_id,
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
            tenant_id=tenant_id,
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
                worker_leases.c.tenant_id == lease.tenant_id,
                worker_leases.c.resource_type == lease.resource_type,
                worker_leases.c.resource_id == lease.resource_id,
                worker_leases.c.owner_id == lease.owner_id,
                worker_leases.c.fence == lease.fence,
            )
            .values(expires_at=datetime.now(UTC))
        )
        async with self._engine.begin() as connection:
            await _set_tenant(connection, lease.tenant_id)
            result = await connection.execute(statement)
        return bool(result.rowcount)
