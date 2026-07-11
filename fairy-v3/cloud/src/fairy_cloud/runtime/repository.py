from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fairy_core.domain.errors import IdempotencyConflictError, WorkerFenceError
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.runtime.models import DynamicRuntimeStart, RuntimeExecutorHealth
from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from fairy_cloud.runtime.models import (
    CloudRuntimeClaim,
    CloudRuntimeClaimAction,
    CloudRuntimeLease,
    CloudRuntimeReviewBinding,
    CloudRuntimeStatus,
)
from fairy_cloud.runtime.tokens import CloudPreviewBinding, CloudPreviewSigner
from fairy_cloud.storage.schema import runtime_leases, runtime_routes, runtime_workers

_EXECUTOR = "cloud_oci_runtime"
_EXECUTOR_VERSION = "1.0.0"


def build_claim_runtime_statement(*, now: datetime):
    observed_at = _aware(now)
    return (
        select(runtime_leases)
        .where(
            runtime_leases.c.expires_at > observed_at,
            or_(
                runtime_leases.c.status == CloudRuntimeStatus.QUEUED.value,
                and_(
                    runtime_leases.c.status.in_(
                        (
                            CloudRuntimeStatus.STARTING.value,
                            CloudRuntimeStatus.RUNNING.value,
                            CloudRuntimeStatus.STOPPING.value,
                        )
                    ),
                    or_(
                        runtime_leases.c.lease_expires_at.is_(None),
                        runtime_leases.c.lease_expires_at <= observed_at,
                    ),
                ),
            ),
        )
        .order_by(runtime_leases.c.created_at, runtime_leases.c.runtime_id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


class CloudRuntimeRepository:
    def __init__(
        self,
        engine: Engine,
        *,
        tenant_id: str,
        lifetime_seconds: int = 3_600,
        health_window_seconds: int = 30,
    ) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("Cloud Runtime leases require PostgreSQL")
        if lifetime_seconds < 60 or health_window_seconds < 1:
            raise ValueError("Cloud Runtime lease timing is invalid")
        self._engine = engine
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._lifetime_seconds = lifetime_seconds
        self._health_window_seconds = health_window_seconds

    def enqueue(self, request: DynamicRuntimeStart) -> CloudRuntimeLease:
        now = datetime.now(UTC)
        candidate = CloudRuntimeLease.create(
            tenant_id=self._tenant_id,
            request=request,
            expires_at=now + timedelta(seconds=self._lifetime_seconds),
            now=now,
        )
        statement = (
            postgres_insert(runtime_leases)
            .values(**_lease_values(candidate))
            .on_conflict_do_nothing(
                index_elements=[runtime_leases.c.tenant_id, runtime_leases.c.runtime_id]
            )
            .returning(runtime_leases)
        )
        with self._engine.begin() as connection:
            _set_tenant(connection, self._tenant_id)
            row = connection.execute(statement).mappings().one_or_none()
            if row is None:
                row = (
                    connection.execute(
                        select(runtime_leases)
                        .where(
                            runtime_leases.c.tenant_id == self._tenant_id,
                            runtime_leases.c.runtime_id == str(request.runtime_id),
                        )
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is not None:
                    existing = _lease_from_row(row)
                    if existing.request_fingerprint != candidate.request_fingerprint:
                        _validate_retry(existing, candidate)
                        row = (
                            connection.execute(
                                update(runtime_leases)
                                .where(
                                    runtime_leases.c.tenant_id == self._tenant_id,
                                    runtime_leases.c.runtime_id == str(request.runtime_id),
                                    runtime_leases.c.status == existing.status.value,
                                    runtime_leases.c.request_lease_fence
                                    == existing.request_lease_fence,
                                )
                                .values(**_retry_values(candidate))
                                .returning(runtime_leases)
                            )
                            .mappings()
                            .one_or_none()
                        )
        if row is None:
            raise RuntimeError("Cloud Runtime enqueue lost its idempotent winner")
        persisted = _lease_from_row(row)
        if persisted.request_fingerprint != candidate.request_fingerprint:
            raise IdempotencyConflictError(
                "Cloud Runtime id is already bound to a different request"
            )
        return persisted

    def get(self, runtime_id: UUID) -> CloudRuntimeLease | None:
        with self._engine.connect() as connection:
            _set_tenant(connection, self._tenant_id)
            row = (
                connection.execute(
                    select(runtime_leases).where(
                        runtime_leases.c.tenant_id == self._tenant_id,
                        runtime_leases.c.runtime_id == str(runtime_id),
                    )
                )
                .mappings()
                .one_or_none()
            )
        return _lease_from_row(row) if row is not None else None

    def request_stop(
        self,
        runtime_id: UUID,
        *,
        request_lease_fence: int,
    ) -> CloudRuntimeLease:
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            _set_tenant(connection, self._tenant_id)
            row = (
                connection.execute(
                    select(runtime_leases)
                    .where(
                        runtime_leases.c.tenant_id == self._tenant_id,
                        runtime_leases.c.runtime_id == str(runtime_id),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise KeyError(f"Cloud Runtime not found: {runtime_id}")
            lease = _lease_from_row(row)
            if lease.request_lease_fence != request_lease_fence:
                raise WorkerFenceError("Cloud Runtime stop request lost its Core fence")
            if lease.status is CloudRuntimeStatus.STOPPED:
                return lease
            changed = (
                connection.execute(
                    update(runtime_leases)
                    .where(
                        runtime_leases.c.tenant_id == self._tenant_id,
                        runtime_leases.c.runtime_id == str(runtime_id),
                        runtime_leases.c.request_lease_fence == request_lease_fence,
                    )
                    .values(
                        status=CloudRuntimeStatus.STOPPING.value,
                        internal_url=None,
                        lease_owner=None,
                        lease_expires_at=None,
                        error_code=None,
                        updated_at=now,
                    )
                    .returning(runtime_leases)
                )
                .mappings()
                .one()
            )
        return _lease_from_row(changed)

    def register_route(self, binding: CloudPreviewBinding, token_hash: str) -> None:
        if binding.tenant_id != self._tenant_id:
            raise WorkerFenceError("Preview route tenant binding does not match")
        now = datetime.now(UTC)
        values = {
            "token_hash": token_hash,
            "tenant_id": binding.tenant_id,
            "runtime_id": str(binding.runtime_id),
            "preview_id": str(binding.preview_id),
            "task_id": str(binding.task_id),
            "version_id": str(binding.version_id),
            "request_lease_fence": binding.lease_fence,
            "expires_at": binding.expires_at,
            "created_at": now,
        }
        statement = (
            postgres_insert(runtime_routes)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[runtime_routes.c.token_hash])
            .returning(runtime_routes)
        )
        with self._engine.begin() as connection:
            _set_tenant(connection, self._tenant_id)
            inserted = connection.execute(statement).mappings().one_or_none()
            if inserted is None:
                inserted = (
                    connection.execute(
                        select(runtime_routes).where(runtime_routes.c.token_hash == token_hash)
                    )
                    .mappings()
                    .one_or_none()
                )
        binding_values = {key: value for key, value in values.items() if key != "created_at"}
        if inserted is None or any(
            inserted.get(key) != value for key, value in binding_values.items()
        ):
            raise WorkerFenceError("Preview route hash is bound to another Runtime")

    def resolve_proxy(self, binding: CloudPreviewBinding) -> str:
        if binding.tenant_id != self._tenant_id:
            raise WorkerFenceError("Preview proxy tenant binding does not match")
        lease = self.get(binding.runtime_id)
        if (
            lease is None
            or lease.status is not CloudRuntimeStatus.RUNNING
            or lease.preview_id != binding.preview_id
            or lease.task_id != binding.task_id
            or lease.version_id != binding.version_id
            or lease.request_lease_fence != binding.lease_fence
            or lease.expires_at <= datetime.now(UTC)
            or lease.internal_url is None
        ):
            raise WorkerFenceError("Preview proxy binding does not match a live Runtime")
        return lease.internal_url

    def health(self) -> RuntimeExecutorHealth:
        threshold = datetime.now(UTC) - timedelta(seconds=self._health_window_seconds)
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(runtime_workers)
                    .where(
                        runtime_workers.c.executor == _EXECUTOR,
                        runtime_workers.c.executor_version == _EXECUTOR_VERSION,
                        runtime_workers.c.last_seen_at >= threshold,
                    )
                    .order_by(runtime_workers.c.last_seen_at.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return RuntimeExecutorHealth(
                available=False,
                executor=_EXECUTOR,
                version=None,
                error_code="SANDBOX_UNAVAILABLE",
                diagnostics=("No current Cloud OCI Runtime worker attestation",),
            )
        return RuntimeExecutorHealth(
            available=True,
            executor=_EXECUTOR,
            version=_EXECUTOR_VERSION,
            error_code=None,
            diagnostics=("Cloud OCI Runtime worker heartbeat verified",),
        )


class CloudPreviewRouteRepository:
    def __init__(self, engine: Engine, *, signer: CloudPreviewSigner) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("Cloud Preview routes require PostgreSQL")
        self._engine = engine
        self._signer = signer

    def resolve(self, token: str, *, now: datetime | None = None) -> str:
        observed_at = (now or datetime.now(UTC)).astimezone(UTC)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(runtime_routes).where(
                        runtime_routes.c.token_hash == token_hash,
                        runtime_routes.c.expires_at > observed_at,
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise WorkerFenceError("Preview route is unknown or expired")
        binding = CloudPreviewBinding(
            tenant_id=str(row["tenant_id"]),
            runtime_id=UUID(str(row["runtime_id"])),
            preview_id=UUID(str(row["preview_id"])),
            task_id=UUID(str(row["task_id"])),
            version_id=UUID(str(row["version_id"])),
            lease_fence=int(row["request_lease_fence"]),
            expires_at=_aware(row["expires_at"]),
        )
        self._signer.verify(token, binding, now=observed_at)
        return CloudRuntimeRepository(
            self._engine,
            tenant_id=binding.tenant_id,
        ).resolve_proxy(binding)


class AsyncCloudRuntimeRepository:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        attestation_digest: str,
        gateway_base_url: str,
    ) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("Cloud Runtime leases require PostgreSQL")
        if len(attestation_digest) != 64 or attestation_digest != attestation_digest.lower():
            raise ValueError("Runtime attestation digest must be lowercase SHA-256")
        if not gateway_base_url.startswith("http://runtime:"):
            raise ValueError("Runtime gateway must use the private runtime service origin")
        self._engine = engine
        self._attestation_digest = attestation_digest
        self._gateway_base_url = gateway_base_url.rstrip("/")
        self._started_at = datetime.now(UTC)

    async def claim_next(
        self,
        *,
        owner_id: str,
        lease_seconds: int,
    ) -> CloudRuntimeClaim | None:
        if not owner_id.strip() or lease_seconds < 1:
            raise ValueError("Runtime claim owner and duration are required")
        now = datetime.now(UTC)
        async with self._engine.begin() as connection:
            row = (
                (await connection.execute(build_claim_runtime_statement(now=now)))
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            current = CloudRuntimeStatus(str(row["status"]))
            action = (
                CloudRuntimeClaimAction.START
                if current is CloudRuntimeStatus.QUEUED
                else CloudRuntimeClaimAction.STOP
                if current is CloudRuntimeStatus.STOPPING
                else CloudRuntimeClaimAction.RECOVER
            )
            status = (
                CloudRuntimeStatus.STARTING.value
                if action is CloudRuntimeClaimAction.START
                else current.value
            )
            claimed = (
                (
                    await connection.execute(
                        update(runtime_leases)
                        .where(
                            runtime_leases.c.tenant_id == row["tenant_id"],
                            runtime_leases.c.runtime_id == row["runtime_id"],
                            runtime_leases.c.lease_fence == row["lease_fence"],
                        )
                        .values(
                            status=status,
                            lease_owner=owner_id,
                            lease_expires_at=now + timedelta(seconds=lease_seconds),
                            lease_fence=int(row["lease_fence"]) + 1,
                            attempts=int(row["attempts"]) + 1,
                            updated_at=now,
                        )
                        .returning(runtime_leases)
                    )
                )
                .mappings()
                .one()
            )
        return CloudRuntimeClaim(_lease_from_row(claimed), action)

    async def active_handle(self, runtime_id: UUID) -> str:
        now = datetime.now(UTC)
        async with self._engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        select(
                            runtime_leases.c.request_lease_fence,
                            runtime_leases.c.expires_at,
                        ).where(
                            runtime_leases.c.runtime_id == str(runtime_id),
                            runtime_leases.c.status == CloudRuntimeStatus.RUNNING.value,
                            runtime_leases.c.expires_at > now,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise KeyError(f"active Cloud Runtime not found: {runtime_id}")
        return f"cloud-dynamic:{runtime_id}:{int(row['request_lease_fence'])}"

    async def active_review_binding(
        self,
        runtime_id: UUID,
    ) -> CloudRuntimeReviewBinding:
        now = datetime.now(UTC)
        async with self._engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        select(
                            runtime_leases.c.runtime_id,
                            runtime_leases.c.preview_id,
                            runtime_leases.c.project_id,
                            runtime_leases.c.conversation_id,
                            runtime_leases.c.task_id,
                            runtime_leases.c.version_id,
                            runtime_leases.c.scope_digest,
                            runtime_leases.c.workspace_generation,
                        ).where(
                            runtime_leases.c.runtime_id == str(runtime_id),
                            runtime_leases.c.status == CloudRuntimeStatus.RUNNING.value,
                            runtime_leases.c.expires_at > now,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise KeyError(f"active Cloud Runtime not found: {runtime_id}")
        return CloudRuntimeReviewBinding(
            runtime_id=UUID(str(row["runtime_id"])),
            preview_id=UUID(str(row["preview_id"])),
            project_id=UUID(str(row["project_id"])),
            conversation_id=UUID(str(row["conversation_id"])),
            task_id=UUID(str(row["task_id"])),
            version_id=UUID(str(row["version_id"])),
            scope_digest=str(row["scope_digest"]),
            workspace_generation=int(row["workspace_generation"]),
        )

    async def mark_running(
        self,
        claim: CloudRuntimeClaim,
        *,
        worker_id: str,
    ) -> None:
        internal_url = f"{self._gateway_base_url}/internal/{claim.lease.runtime_id}/"
        changed = await self._fenced_update(
            claim,
            statuses={CloudRuntimeStatus.STARTING, CloudRuntimeStatus.RUNNING},
            values={
                "status": CloudRuntimeStatus.RUNNING.value,
                "internal_url": internal_url,
                "worker_id": worker_id,
                "error_code": None,
                "updated_at": datetime.now(UTC),
            },
        )
        _require_fence(changed, "mark running")

    async def mark_stopped(self, claim: CloudRuntimeClaim) -> None:
        changed = await self._fenced_update(
            claim,
            statuses={CloudRuntimeStatus.STOPPING},
            values={
                "status": CloudRuntimeStatus.STOPPED.value,
                "internal_url": None,
                "lease_owner": None,
                "lease_expires_at": None,
                "error_code": None,
                "updated_at": datetime.now(UTC),
            },
        )
        _require_fence(changed, "mark stopped")

    async def mark_interrupted(
        self,
        claim: CloudRuntimeClaim,
        *,
        error_code: str = "WORKER_INTERRUPTED",
    ) -> None:
        changed = await self._fenced_update(
            claim,
            statuses={
                CloudRuntimeStatus.STARTING,
                CloudRuntimeStatus.RUNNING,
                CloudRuntimeStatus.STOPPING,
            },
            values={
                "status": CloudRuntimeStatus.INTERRUPTED.value,
                "internal_url": None,
                "lease_owner": None,
                "lease_expires_at": None,
                "error_code": error_code,
                "updated_at": datetime.now(UTC),
            },
        )
        _require_fence(changed, "mark interrupted")

    async def expire_due(self) -> int:
        now = datetime.now(UTC)
        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(runtime_leases)
                .where(
                    runtime_leases.c.expires_at <= now,
                    runtime_leases.c.status.in_(
                        (
                            CloudRuntimeStatus.QUEUED.value,
                            CloudRuntimeStatus.STARTING.value,
                            CloudRuntimeStatus.RUNNING.value,
                        )
                    ),
                )
                .values(
                    status=CloudRuntimeStatus.STOPPING.value,
                    internal_url=None,
                    lease_owner=None,
                    lease_expires_at=None,
                    updated_at=now,
                )
            )
        return int(result.rowcount or 0)

    async def heartbeat(self, *, owner_id: str) -> None:
        now = datetime.now(UTC)
        statement = postgres_insert(runtime_workers).values(
            owner_id=owner_id,
            executor=_EXECUTOR,
            executor_version=_EXECUTOR_VERSION,
            attestation_digest=self._attestation_digest,
            gateway_base_url=self._gateway_base_url,
            started_at=self._started_at,
            last_seen_at=now,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[runtime_workers.c.owner_id],
            set_={
                "executor": _EXECUTOR,
                "executor_version": _EXECUTOR_VERSION,
                "attestation_digest": self._attestation_digest,
                "gateway_base_url": self._gateway_base_url,
                "last_seen_at": now,
            },
        )
        async with self._engine.begin() as connection:
            await connection.execute(statement)

    async def _fenced_update(
        self,
        claim: CloudRuntimeClaim,
        *,
        statuses: set[CloudRuntimeStatus],
        values: Mapping[str, object],
    ) -> int:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(runtime_leases)
                .where(
                    runtime_leases.c.tenant_id == claim.lease.tenant_id,
                    runtime_leases.c.runtime_id == str(claim.lease.runtime_id),
                    runtime_leases.c.status.in_(tuple(value.value for value in statuses)),
                    runtime_leases.c.lease_owner == claim.lease.lease_owner,
                    runtime_leases.c.lease_fence == claim.lease.lease_fence,
                )
                .values(**dict(values))
            )
        return int(result.rowcount or 0)


def _lease_values(lease: CloudRuntimeLease) -> dict[str, object]:
    return {
        "tenant_id": lease.tenant_id,
        "runtime_id": str(lease.runtime_id),
        "preview_id": str(lease.preview_id),
        "project_id": str(lease.project_id),
        "conversation_id": str(lease.conversation_id),
        "task_id": str(lease.task_id),
        "version_id": str(lease.version_id),
        "scope_digest": lease.scope_digest,
        "request_fingerprint": lease.request_fingerprint,
        "workspace_generation": lease.workspace_generation,
        "request_lease_fence": lease.request_lease_fence,
        "adapter": lease.adapter,
        "argv": list(lease.argv),
        "cwd": lease.cwd,
        "readiness_path": lease.readiness_path,
        "startup_timeout_seconds": lease.startup_timeout_seconds,
        "dependency_key": lease.dependency_key,
        "workspace_archive": lease.workspace_archive,
        "archive_sha256": lease.archive_sha256,
        "archive_byte_length": len(lease.workspace_archive),
        "status": lease.status.value,
        "internal_url": lease.internal_url,
        "worker_id": lease.worker_id,
        "lease_owner": lease.lease_owner,
        "lease_expires_at": lease.lease_expires_at,
        "lease_fence": lease.lease_fence,
        "attempts": lease.attempts,
        "expires_at": lease.expires_at,
        "error_code": lease.error_code,
        "created_at": lease.created_at,
        "updated_at": lease.updated_at,
    }


def _validate_retry(
    existing: CloudRuntimeLease,
    candidate: CloudRuntimeLease,
) -> None:
    immutable_binding = (
        "tenant_id",
        "runtime_id",
        "preview_id",
        "project_id",
        "conversation_id",
        "task_id",
        "version_id",
        "scope_digest",
    )
    if (
        existing.status not in {CloudRuntimeStatus.FAILED, CloudRuntimeStatus.INTERRUPTED}
        or candidate.request_lease_fence <= existing.request_lease_fence
        or any(getattr(existing, name) != getattr(candidate, name) for name in immutable_binding)
    ):
        raise IdempotencyConflictError(
            "Cloud Runtime id is already bound to a different active request"
        )


def _retry_values(candidate: CloudRuntimeLease) -> dict[str, object]:
    return {
        "request_fingerprint": candidate.request_fingerprint,
        "workspace_generation": candidate.workspace_generation,
        "request_lease_fence": candidate.request_lease_fence,
        "adapter": candidate.adapter,
        "argv": list(candidate.argv),
        "cwd": candidate.cwd,
        "readiness_path": candidate.readiness_path,
        "startup_timeout_seconds": candidate.startup_timeout_seconds,
        "dependency_key": candidate.dependency_key,
        "workspace_archive": candidate.workspace_archive,
        "archive_sha256": candidate.archive_sha256,
        "archive_byte_length": len(candidate.workspace_archive),
        "status": CloudRuntimeStatus.QUEUED.value,
        "internal_url": None,
        "worker_id": None,
        "lease_owner": None,
        "lease_expires_at": None,
        "expires_at": candidate.expires_at,
        "error_code": None,
        "updated_at": candidate.updated_at,
    }


def _lease_from_row(row: Mapping[str, object]) -> CloudRuntimeLease:
    lease = CloudRuntimeLease(
        tenant_id=str(row["tenant_id"]),
        runtime_id=UUID(str(row["runtime_id"])),
        preview_id=UUID(str(row["preview_id"])),
        project_id=UUID(str(row["project_id"])),
        conversation_id=UUID(str(row["conversation_id"])),
        task_id=UUID(str(row["task_id"])),
        version_id=UUID(str(row["version_id"])),
        scope_digest=str(row["scope_digest"]),
        request_fingerprint=str(row["request_fingerprint"]),
        workspace_generation=int(row["workspace_generation"]),
        request_lease_fence=int(row["request_lease_fence"]),
        adapter=str(row["adapter"]),
        argv=tuple(str(value) for value in row["argv"]),
        cwd=str(row["cwd"]),
        readiness_path=str(row["readiness_path"]),
        startup_timeout_seconds=int(row["startup_timeout_seconds"]),
        dependency_key=str(row["dependency_key"]),
        workspace_archive=bytes(row["workspace_archive"]),
        archive_sha256=str(row["archive_sha256"]),
        status=CloudRuntimeStatus(str(row["status"])),
        internal_url=str(row["internal_url"]) if row.get("internal_url") else None,
        worker_id=str(row["worker_id"]) if row.get("worker_id") else None,
        lease_owner=str(row["lease_owner"]) if row.get("lease_owner") else None,
        lease_expires_at=_optional_datetime(row.get("lease_expires_at")),
        lease_fence=int(row["lease_fence"]),
        attempts=int(row["attempts"]),
        expires_at=_aware(row["expires_at"]),
        error_code=str(row["error_code"]) if row.get("error_code") else None,
        created_at=_aware(row["created_at"]),
        updated_at=_aware(row["updated_at"]),
    )
    if row.get("archive_byte_length") != len(lease.workspace_archive):
        raise ValueError("Cloud Runtime archive length does not match")
    if lease.to_request().archive_sha256 != lease.archive_sha256:
        raise ValueError("Cloud Runtime archive hash does not match")
    return lease


def _set_tenant(connection, tenant_id: str) -> None:
    connection.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Cloud Runtime timestamp requires a timezone")
    return value.astimezone(UTC)


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _aware(value)


def _require_fence(changed: int, action: str) -> None:
    if changed != 1:
        raise WorkerFenceError(f"Cloud Runtime worker lost its fence during {action}")


__all__ = [
    "AsyncCloudRuntimeRepository",
    "CloudPreviewRouteRepository",
    "CloudRuntimeRepository",
    "build_claim_runtime_statement",
]
