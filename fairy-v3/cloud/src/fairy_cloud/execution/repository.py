from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fairy_core.domain.errors import IdempotencyConflictError, WorkerFenceError
from fairy_core.runtime.models import RuntimeExecutorHealth
from fairy_core.sandbox.models import (
    SandboxNetworkPolicy,
    SandboxPurpose,
    SandboxRequest,
    SandboxResult,
    SandboxResultStatus,
)
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql import Select

from fairy_cloud.execution.models import (
    ExecutionClaim,
    ExecutionClaimAction,
    ExecutionJob,
    ExecutionJobStatus,
    execution_request_fingerprint,
)
from fairy_cloud.storage.schema import execution_jobs, execution_workers

_EXECUTOR = "cloud_oci_worker"
_EXECUTOR_VERSION = "1.1.0"


class ExecutionCancelledBeforeSpawn(RuntimeError):
    pass


def build_claim_execution_statement(*, now: datetime) -> Select:
    observed_at = _aware(now)
    return (
        select(execution_jobs)
        .where(
            or_(
                execution_jobs.c.status == ExecutionJobStatus.QUEUED.value,
                and_(
                    execution_jobs.c.status == ExecutionJobStatus.CLAIMED.value,
                    execution_jobs.c.spawned_at.is_(None),
                    execution_jobs.c.lease_expires_at <= observed_at,
                ),
                and_(
                    execution_jobs.c.status == ExecutionJobStatus.RESULT_RECORDED.value,
                    or_(
                        execution_jobs.c.lease_expires_at.is_(None),
                        execution_jobs.c.lease_expires_at <= observed_at,
                    ),
                ),
            )
        )
        .order_by(execution_jobs.c.created_at, execution_jobs.c.job_id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


class ExecutionJobRepository:
    def __init__(
        self,
        engine: Engine,
        *,
        tenant_id: str,
        health_window_seconds: int = 30,
    ) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("Cloud execution jobs require PostgreSQL")
        if health_window_seconds < 1:
            raise ValueError("health_window_seconds must be positive")
        self._engine = engine
        self._tenant_id = tenant_id
        self._health_window_seconds = health_window_seconds

    def enqueue(self, request: SandboxRequest) -> ExecutionJob:
        candidate = ExecutionJob.create(tenant_id=self._tenant_id, request=request)
        statement = (
            postgres_insert(execution_jobs)
            .values(**_job_values(candidate))
            .on_conflict_do_nothing(
                index_elements=[execution_jobs.c.tenant_id, execution_jobs.c.job_id]
            )
            .returning(execution_jobs)
        )
        with self._engine.begin() as connection:
            _set_tenant(connection, self._tenant_id)
            inserted = connection.execute(statement).mappings().one_or_none()
            if inserted is not None:
                return _job_from_row(inserted)
            existing = (
                connection.execute(
                    select(execution_jobs).where(
                        execution_jobs.c.tenant_id == self._tenant_id,
                        execution_jobs.c.job_id == str(request.job_id),
                    )
                )
                .mappings()
                .one_or_none()
            )
        if existing is None:
            raise RuntimeError("Execution job enqueue lost its idempotent winner")
        persisted = _job_from_row(existing)
        if persisted.request_fingerprint != candidate.request_fingerprint:
            raise IdempotencyConflictError(
                "Execution job id is already bound to a different request"
            )
        return persisted

    def get(self, job_id: UUID) -> ExecutionJob | None:
        with self._engine.connect() as connection:
            _set_tenant(connection, self._tenant_id)
            row = (
                connection.execute(
                    select(execution_jobs).where(
                        execution_jobs.c.tenant_id == self._tenant_id,
                        execution_jobs.c.job_id == str(job_id),
                    )
                )
                .mappings()
                .one_or_none()
            )
        return _job_from_row(row) if row is not None else None

    def request_cancel(self, job_id: UUID) -> None:
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            _set_tenant(connection, self._tenant_id)
            row = (
                connection.execute(
                    select(execution_jobs)
                    .where(
                        execution_jobs.c.tenant_id == self._tenant_id,
                        execution_jobs.c.job_id == str(job_id),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise KeyError(f"Execution job not found: {job_id}")
            status = ExecutionJobStatus(str(row["status"]))
            if status.terminal:
                return
            values: dict[str, object] = {
                "cancel_requested": True,
                "updated_at": now,
            }
            if status is ExecutionJobStatus.QUEUED:
                values.update(
                    status=ExecutionJobStatus.CANCELLED.value,
                    finished_at=now,
                )
            connection.execute(
                update(execution_jobs)
                .where(
                    execution_jobs.c.tenant_id == self._tenant_id,
                    execution_jobs.c.job_id == str(job_id),
                )
                .values(**values)
            )

    def health(self) -> RuntimeExecutorHealth:
        threshold = datetime.now(UTC) - timedelta(seconds=self._health_window_seconds)
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(execution_workers)
                    .where(
                        execution_workers.c.executor == _EXECUTOR,
                        execution_workers.c.executor_version == _EXECUTOR_VERSION,
                        execution_workers.c.last_seen_at >= threshold,
                    )
                    .order_by(execution_workers.c.last_seen_at.desc())
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
                diagnostics=("No current Cloud OCI execution worker attestation",),
            )
        return RuntimeExecutorHealth(
            available=True,
            executor=_EXECUTOR,
            version=_EXECUTOR_VERSION,
            error_code=None,
            diagnostics=("Cloud OCI execution worker heartbeat verified",),
        )


class AsyncExecutionJobRepository:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        attestation_digest: str,
    ) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("Cloud execution jobs require PostgreSQL")
        if len(attestation_digest) != 64 or attestation_digest != attestation_digest.lower():
            raise ValueError("attestation_digest must be lowercase SHA-256")
        self._engine = engine
        self._attestation_digest = attestation_digest
        self._started_at = datetime.now(UTC)

    async def claim_next(
        self,
        *,
        owner_id: str,
        lease_seconds: int,
    ) -> ExecutionClaim | None:
        if not owner_id.strip() or lease_seconds < 1:
            raise ValueError("claim owner and positive lease are required")
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=lease_seconds)
        async with self._engine.begin() as connection:
            row = (
                (await connection.execute(build_claim_execution_statement(now=now)))
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            action = (
                ExecutionClaimAction.FINALIZE
                if row["status"] == ExecutionJobStatus.RESULT_RECORDED.value
                else ExecutionClaimAction.EXECUTE
            )
            next_status = (
                ExecutionJobStatus.RESULT_RECORDED.value
                if action is ExecutionClaimAction.FINALIZE
                else ExecutionJobStatus.CLAIMED.value
            )
            values = {
                "status": next_status,
                "lease_owner": owner_id,
                "lease_expires_at": expires_at,
                "lease_fence": int(row["lease_fence"]) + 1,
                "updated_at": now,
            }
            if action is ExecutionClaimAction.EXECUTE:
                values["attempts"] = int(row["attempts"]) + 1
            claimed = (
                (
                    await connection.execute(
                        update(execution_jobs)
                        .where(
                            execution_jobs.c.tenant_id == row["tenant_id"],
                            execution_jobs.c.job_id == row["job_id"],
                            execution_jobs.c.lease_fence == row["lease_fence"],
                        )
                        .values(**values)
                        .returning(execution_jobs)
                    )
                )
                .mappings()
                .one()
            )
        return ExecutionClaim(job=_job_from_row(claimed), action=action)

    async def mark_spawned(self, claim: ExecutionClaim) -> None:
        now = datetime.now(UTC)
        result = await self._fenced_update(
            claim,
            expected_status=ExecutionJobStatus.CLAIMED,
            require_not_cancelled=True,
            values={
                "status": ExecutionJobStatus.RUNNING.value,
                "spawned_at": now,
                "lease_expires_at": now + timedelta(seconds=claim.job.timeout_seconds + 30),
                "updated_at": now,
            },
        )
        if result == 0:
            if await self.cancellation_requested(claim):
                raise ExecutionCancelledBeforeSpawn("Execution was cancelled before spawn")
            raise WorkerFenceError("Execution claim lost its fence before spawn")

    async def cancellation_requested(self, claim: ExecutionClaim) -> bool:
        async with self._engine.connect() as connection:
            value = (
                await connection.execute(
                    select(execution_jobs.c.cancel_requested).where(
                        execution_jobs.c.tenant_id == claim.job.tenant_id,
                        execution_jobs.c.job_id == str(claim.job.job_id),
                        execution_jobs.c.lease_owner == claim.job.lease_owner,
                        execution_jobs.c.lease_fence == claim.job.lease_fence,
                    )
                )
            ).scalar_one_or_none()
        if value is None:
            raise WorkerFenceError("Execution cancellation check lost its fence")
        return bool(value)

    async def mark_cancelled(self, claim: ExecutionClaim) -> None:
        now = datetime.now(UTC)
        changed = await self._fenced_update(
            claim,
            expected_status=ExecutionJobStatus.CLAIMED,
            values={
                "status": ExecutionJobStatus.CANCELLED.value,
                "cancel_requested": True,
                "finished_at": now,
                "lease_owner": None,
                "lease_expires_at": None,
                "updated_at": now,
            },
        )
        _require_fence(changed, "cancel")

    async def record_result(
        self,
        claim: ExecutionClaim,
        result: SandboxResult,
    ) -> None:
        request = claim.job.to_request()
        if (
            result.job_id != request.job_id
            or result.scope_digest != request.scope_digest
            or result.workspace_generation != request.workspace_generation
            or result.lease_fence != request.lease_fence
        ):
            raise WorkerFenceError("Execution result does not match its request binding")
        now = datetime.now(UTC)
        changed = await self._fenced_update(
            claim,
            expected_status=ExecutionJobStatus.RUNNING,
            values={
                "status": ExecutionJobStatus.RESULT_RECORDED.value,
                "result_status": result.status.value,
                "executor": result.executor,
                "executor_version": result.executor_version,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "stdout_sha256": result.stdout_sha256,
                "stderr_sha256": result.stderr_sha256,
                "output_truncated": result.output_truncated,
                "started_at": result.started_at,
                "result_recorded_at": now,
                "finished_at": result.finished_at,
                "updated_at": now,
            },
        )
        _require_fence(changed, "record result")

    async def finalize_result(self, claim: ExecutionClaim) -> None:
        async with self._engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        select(execution_jobs.c.result_status).where(
                            execution_jobs.c.tenant_id == claim.job.tenant_id,
                            execution_jobs.c.job_id == str(claim.job.job_id),
                            execution_jobs.c.status == ExecutionJobStatus.RESULT_RECORDED.value,
                            execution_jobs.c.lease_owner == claim.job.lease_owner,
                            execution_jobs.c.lease_fence == claim.job.lease_fence,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise WorkerFenceError("Execution finalizer lost its fence")
            terminal = _terminal_status(str(row["result_status"]))
            changed = await connection.execute(
                update(execution_jobs)
                .where(
                    execution_jobs.c.tenant_id == claim.job.tenant_id,
                    execution_jobs.c.job_id == str(claim.job.job_id),
                    execution_jobs.c.status == ExecutionJobStatus.RESULT_RECORDED.value,
                    execution_jobs.c.lease_owner == claim.job.lease_owner,
                    execution_jobs.c.lease_fence == claim.job.lease_fence,
                )
                .values(
                    status=terminal.value,
                    lease_owner=None,
                    lease_expires_at=None,
                    updated_at=func.now(),
                )
            )
            _require_fence(int(changed.rowcount or 0), "finalize")

    async def mark_interrupted(self, claim: ExecutionClaim) -> None:
        now = datetime.now(UTC)
        async with self._engine.begin() as connection:
            await connection.execute(
                update(execution_jobs)
                .where(
                    execution_jobs.c.tenant_id == claim.job.tenant_id,
                    execution_jobs.c.job_id == str(claim.job.job_id),
                    execution_jobs.c.status.in_(
                        [
                            ExecutionJobStatus.CLAIMED.value,
                            ExecutionJobStatus.RUNNING.value,
                        ]
                    ),
                    execution_jobs.c.lease_owner == claim.job.lease_owner,
                    execution_jobs.c.lease_fence == claim.job.lease_fence,
                )
                .values(
                    status=ExecutionJobStatus.INTERRUPTED.value,
                    finished_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                    updated_at=now,
                )
            )

    async def reconcile_expired(self) -> int:
        now = datetime.now(UTC)
        async with self._engine.begin() as connection:
            interrupted = await connection.execute(
                update(execution_jobs)
                .where(
                    execution_jobs.c.status == ExecutionJobStatus.RUNNING.value,
                    execution_jobs.c.spawned_at.is_not(None),
                    execution_jobs.c.lease_expires_at <= now,
                )
                .values(
                    status=ExecutionJobStatus.INTERRUPTED.value,
                    finished_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                    updated_at=now,
                )
            )
            cancelled = await connection.execute(
                update(execution_jobs)
                .where(
                    execution_jobs.c.status == ExecutionJobStatus.CLAIMED.value,
                    execution_jobs.c.spawned_at.is_(None),
                    execution_jobs.c.cancel_requested.is_(True),
                    execution_jobs.c.lease_expires_at <= now,
                )
                .values(
                    status=ExecutionJobStatus.CANCELLED.value,
                    finished_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                    updated_at=now,
                )
            )
        return int(interrupted.rowcount or 0) + int(cancelled.rowcount or 0)

    async def heartbeat(self, *, owner_id: str) -> None:
        if not owner_id.strip():
            raise ValueError("heartbeat owner is required")
        now = datetime.now(UTC)
        async with self._engine.begin() as connection:
            await connection.execute(
                postgres_insert(execution_workers)
                .values(
                    owner_id=owner_id,
                    executor=_EXECUTOR,
                    executor_version=_EXECUTOR_VERSION,
                    attestation_digest=self._attestation_digest,
                    started_at=self._started_at,
                    last_seen_at=now,
                )
                .on_conflict_do_update(
                    index_elements=[execution_workers.c.owner_id],
                    set_={
                        "executor": _EXECUTOR,
                        "executor_version": _EXECUTOR_VERSION,
                        "attestation_digest": self._attestation_digest,
                        "last_seen_at": now,
                    },
                )
            )

    async def _fenced_update(
        self,
        claim: ExecutionClaim,
        *,
        expected_status: ExecutionJobStatus,
        values: Mapping[str, object],
        require_not_cancelled: bool = False,
    ) -> int:
        predicates = [
            execution_jobs.c.tenant_id == claim.job.tenant_id,
            execution_jobs.c.job_id == str(claim.job.job_id),
            execution_jobs.c.status == expected_status.value,
            execution_jobs.c.lease_owner == claim.job.lease_owner,
            execution_jobs.c.lease_fence == claim.job.lease_fence,
        ]
        if require_not_cancelled:
            predicates.append(execution_jobs.c.cancel_requested.is_(False))
        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(execution_jobs).where(*predicates).values(**dict(values))
            )
        return int(result.rowcount or 0)


def _job_values(job: ExecutionJob) -> dict[str, object]:
    return {
        "tenant_id": job.tenant_id,
        "job_id": str(job.job_id),
        "command_run_id": str(job.command_run_id),
        "project_id": str(job.project_id) if job.project_id else None,
        "conversation_id": str(job.conversation_id),
        "task_id": str(job.task_id),
        "version_id": str(job.version_id) if job.version_id else None,
        "scope_digest": job.scope_digest,
        "request_fingerprint": job.request_fingerprint,
        "workspace_generation": job.workspace_generation,
        "request_lease_fence": job.request_lease_fence,
        "argv": list(job.argv),
        "cwd": job.cwd,
        "environment": dict(job.environment),
        "timeout_seconds": job.timeout_seconds,
        "output_limit_bytes": job.output_limit_bytes,
        "network_policy": job.network_policy.value,
        "purpose": job.purpose.value,
        "dependency_key": job.dependency_key,
        "dependency_manager": job.dependency_manager,
        "workspace_archive": job.workspace_archive,
        "archive_sha256": job.archive_sha256,
        "archive_byte_length": len(job.workspace_archive),
        "status": job.status.value,
        "cancel_requested": job.cancel_requested,
        "attempts": job.attempts,
        "lease_fence": job.lease_fence,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _job_from_row(row: Mapping[str, object]) -> ExecutionJob:
    job = ExecutionJob(
        tenant_id=str(row["tenant_id"]),
        job_id=UUID(str(row["job_id"])),
        command_run_id=UUID(str(row["command_run_id"])),
        project_id=_optional_uuid(row.get("project_id")),
        conversation_id=UUID(str(row["conversation_id"])),
        task_id=UUID(str(row["task_id"])),
        version_id=_optional_uuid(row.get("version_id")),
        scope_digest=str(row["scope_digest"]),
        request_fingerprint=str(row["request_fingerprint"]),
        workspace_generation=int(row["workspace_generation"]),
        request_lease_fence=int(row["request_lease_fence"]),
        argv=tuple(str(item) for item in row["argv"]),
        cwd=str(row["cwd"]),
        environment={str(key): str(value) for key, value in dict(row["environment"]).items()},
        timeout_seconds=int(row["timeout_seconds"]),
        output_limit_bytes=int(row["output_limit_bytes"]),
        network_policy=SandboxNetworkPolicy(str(row["network_policy"])),
        purpose=SandboxPurpose(str(row["purpose"])),
        dependency_key=(
            str(row["dependency_key"]) if row.get("dependency_key") is not None else None
        ),
        dependency_manager=(
            str(row["dependency_manager"]) if row.get("dependency_manager") is not None else None
        ),
        workspace_archive=bytes(row["workspace_archive"]),
        archive_sha256=str(row["archive_sha256"]),
        status=ExecutionJobStatus(str(row["status"])),
        cancel_requested=bool(row["cancel_requested"]),
        attempts=int(row["attempts"]),
        lease_owner=str(row["lease_owner"]) if row.get("lease_owner") else None,
        lease_expires_at=_optional_datetime(row.get("lease_expires_at")),
        lease_fence=int(row["lease_fence"]),
        spawned_at=_optional_datetime(row.get("spawned_at")),
        result_status=(
            SandboxResultStatus(str(row["result_status"])) if row.get("result_status") else None
        ),
        executor=str(row["executor"]) if row.get("executor") else None,
        executor_version=(str(row["executor_version"]) if row.get("executor_version") else None),
        exit_code=int(row["exit_code"]) if row.get("exit_code") is not None else None,
        stdout=bytes(row["stdout"]) if row.get("stdout") is not None else None,
        stderr=bytes(row["stderr"]) if row.get("stderr") is not None else None,
        output_truncated=(
            bool(row["output_truncated"]) if row.get("output_truncated") is not None else None
        ),
        started_at=_optional_datetime(row.get("started_at")),
        result_recorded_at=_optional_datetime(row.get("result_recorded_at")),
        finished_at=_optional_datetime(row.get("finished_at")),
        created_at=_aware(row["created_at"]),
        updated_at=_aware(row["updated_at"]),
    )
    _validate_job_integrity(job, row)
    return job


def _validate_job_integrity(job: ExecutionJob, row: Mapping[str, object]) -> None:
    request = job.to_request()
    archive_length = row.get("archive_byte_length")
    if (
        isinstance(archive_length, bool)
        or not isinstance(archive_length, int)
        or archive_length != len(job.workspace_archive)
        or not hmac.compare_digest(request.archive_sha256, job.archive_sha256)
    ):
        raise ValueError("Execution workspace archive integrity check failed")
    if not hmac.compare_digest(
        execution_request_fingerprint(request),
        job.request_fingerprint,
    ):
        raise ValueError("Execution request fingerprint integrity check failed")

    result_values = (
        job.result_status,
        job.executor,
        job.executor_version,
        job.stdout,
        job.stderr,
        job.output_truncated,
        job.started_at,
        job.result_recorded_at,
    )
    if job.result_status is None:
        if any(value is not None for value in result_values[1:]):
            raise ValueError("Execution row has partial result evidence")
        return
    if any(value is None for value in result_values[1:]):
        raise ValueError("Execution row has incomplete result evidence")
    if job.executor != _EXECUTOR or job.executor_version != _EXECUTOR_VERSION:
        raise ValueError("Execution result executor identity does not match")
    assert job.stdout is not None
    assert job.stderr is not None
    assert job.output_truncated is not None
    assert job.started_at is not None
    assert job.finished_at is not None
    stdout_hash = row.get("stdout_sha256")
    stderr_hash = row.get("stderr_sha256")
    if not _matches_hash(job.stdout, stdout_hash):
        raise ValueError("Execution stdout hash does not match")
    if not _matches_hash(job.stderr, stderr_hash):
        raise ValueError("Execution stderr hash does not match")
    SandboxResult.create(
        request=request,
        executor=job.executor,
        executor_version=job.executor_version,
        status=job.result_status,
        exit_code=job.exit_code,
        stdout=job.stdout,
        stderr=job.stderr,
        output_truncated=job.output_truncated,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )
    expected_terminal = {
        ExecutionJobStatus.SUCCEEDED: SandboxResultStatus.COMPLETED,
        ExecutionJobStatus.FAILED: SandboxResultStatus.FAILED,
        ExecutionJobStatus.TIMED_OUT: SandboxResultStatus.TIMED_OUT,
        ExecutionJobStatus.CANCELLED: SandboxResultStatus.CANCELLED,
    }.get(job.status)
    if expected_terminal is not None and job.result_status is not expected_terminal:
        raise ValueError("Execution terminal status does not match its result")


def _matches_hash(value: bytes, expected: object) -> bool:
    return isinstance(expected, str) and hmac.compare_digest(
        hashlib.sha256(value).hexdigest(),
        expected,
    )


def _terminal_status(result_status: str) -> ExecutionJobStatus:
    return {
        "completed": ExecutionJobStatus.SUCCEEDED,
        "failed": ExecutionJobStatus.FAILED,
        "timed_out": ExecutionJobStatus.TIMED_OUT,
        "cancelled": ExecutionJobStatus.CANCELLED,
    }[result_status]


def _optional_uuid(value: object) -> UUID | None:
    return UUID(str(value)) if value is not None else None


def _optional_datetime(value: object) -> datetime | None:
    return _aware(value) if value is not None else None


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("Execution timestamp is invalid")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _set_tenant(connection, tenant_id: str) -> None:
    connection.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )


def _require_fence(changed: int, action: str) -> None:
    if changed != 1:
        raise WorkerFenceError(f"Execution worker lost its fence during {action}")


__all__ = [
    "AsyncExecutionJobRepository",
    "ExecutionCancelledBeforeSpawn",
    "ExecutionJobRepository",
    "build_claim_execution_statement",
]
