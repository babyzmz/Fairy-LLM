from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fairy_core.domain.errors import IdempotencyConflictError, WorkerFenceError
from fairy_core.runtime.models import RuntimeExecutorHealth
from fairy_core.sandbox.models import (
    SandboxNetworkPolicy,
    SandboxPurpose,
    SandboxRequest,
    SandboxResult,
    SandboxResultStatus,
)
from sqlalchemy.dialects import postgresql

from fairy_cloud.execution.executor import CloudSandboxExecutor, SecretEgressBlockedError
from fairy_cloud.execution.models import (
    ExecutionClaim,
    ExecutionClaimAction,
    ExecutionJob,
    ExecutionJobStatus,
)
from fairy_cloud.execution.repository import (
    _job_from_row,
    _job_values,
    build_claim_execution_statement,
)
from fairy_cloud.workers.execution import ExecutionWorker, decode_sandbox_result


def _request(
    *,
    network: SandboxNetworkPolicy = SandboxNetworkPolicy.NONE,
    purpose: SandboxPurpose = SandboxPurpose.RAW,
) -> SandboxRequest:
    dependency_aware = purpose in {SandboxPurpose.DEPENDENCY, SandboxPurpose.REVIEW}
    return SandboxRequest.create(
        job_id=uuid4(),
        project_id=uuid4(),
        conversation_id=uuid4(),
        task_id=uuid4(),
        version_id=uuid4(),
        scope_digest="a" * 64,
        workspace_generation=3,
        lease_fence=7,
        argv=("python3", "-m", "pytest", "-q"),
        cwd=".",
        environment={"CI": "1"},
        timeout_seconds=60,
        output_limit_bytes=65_536,
        network_policy=network,
        workspace_archive=b"PK\x03\x04cloud-fixture",
        purpose=purpose,
        dependency_key="b" * 64 if dependency_aware else None,
        dependency_manager="npm" if dependency_aware else None,
    )


def _completed(job: ExecutionJob, *, stdout: bytes = b"ok\n") -> ExecutionJob:
    now = datetime.now(UTC)
    return replace(
        job,
        status=ExecutionJobStatus.SUCCEEDED,
        result_status=SandboxResultStatus.COMPLETED,
        executor="cloud_oci_worker",
        executor_version="1.1.0",
        exit_code=0,
        stdout=stdout,
        stderr=b"",
        output_truncated=False,
        started_at=now,
        finished_at=now,
        updated_at=now,
    )


class SyncStore:
    def __init__(self, *, healthy: bool = True) -> None:
        self.healthy = healthy
        self.enqueued: list[SandboxRequest] = []
        self.jobs: dict[UUID, ExecutionJob] = {}
        self.cancelled: list[UUID] = []

    def health(self) -> RuntimeExecutorHealth:
        return RuntimeExecutorHealth(
            available=self.healthy,
            executor="cloud_oci_worker",
            version="1.1.0" if self.healthy else None,
            error_code=None if self.healthy else "SANDBOX_UNAVAILABLE",
            diagnostics=("fixture",),
        )

    def enqueue(self, request: SandboxRequest) -> ExecutionJob:
        self.enqueued.append(request)
        job = self.jobs.setdefault(
            request.job_id,
            ExecutionJob.create(tenant_id="tenant-a", request=request),
        )
        self.jobs[request.job_id] = _completed(job)
        return self.jobs[request.job_id]

    def get(self, job_id: UUID) -> ExecutionJob | None:
        return self.jobs.get(job_id)

    def request_cancel(self, job_id: UUID) -> None:
        self.cancelled.append(job_id)


def test_execution_job_round_trips_the_core_injected_request_and_fingerprint() -> None:
    request = _request()
    job = ExecutionJob.create(tenant_id="tenant-a", request=request)

    assert job.job_id == request.job_id
    assert job.command_run_id == request.job_id
    assert job.scope_digest == request.scope_digest
    assert job.request_lease_fence == request.lease_fence
    assert job.archive_sha256 == request.archive_sha256
    assert job.to_request() == request
    assert len(job.request_fingerprint) == 64

    changed = ExecutionJob.create(
        tenant_id="tenant-a",
        request=_request(),
    )
    assert changed.request_fingerprint != job.request_fingerprint


def test_repository_row_reader_rejects_workspace_archive_corruption() -> None:
    job = ExecutionJob.create(tenant_id="tenant-a", request=_request())
    row = _job_values(job)
    row["workspace_archive"] = b"tampered"

    with pytest.raises(ValueError, match="archive"):
        _job_from_row(row)


def test_repository_row_reader_rejects_result_output_hash_corruption() -> None:
    job = _completed(ExecutionJob.create(tenant_id="tenant-a", request=_request()))
    row = _job_values(job)
    row.update(
        status=job.status.value,
        result_status=job.result_status.value if job.result_status else None,
        executor=job.executor,
        executor_version=job.executor_version,
        exit_code=job.exit_code,
        stdout=job.stdout,
        stderr=job.stderr,
        stdout_sha256="0" * 64,
        stderr_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        output_truncated=job.output_truncated,
        started_at=job.started_at,
        result_recorded_at=job.finished_at,
        finished_at=job.finished_at,
    )

    with pytest.raises(ValueError, match="stdout"):
        _job_from_row(row)


def test_cloud_executor_enqueues_once_and_returns_a_bound_terminal_result() -> None:
    store = SyncStore()
    executor = CloudSandboxExecutor(store=store, poll_seconds=0, wait_grace_seconds=1)
    request = _request()

    result = executor.execute(request)

    assert len(store.enqueued) == 1
    assert result.job_id == request.job_id
    assert result.executor == "cloud_oci_worker"
    assert result.stdout == b"ok\n"

    executor.cancel(request.job_id)
    assert store.cancelled == [request.job_id]


def test_cloud_executor_fails_closed_for_public_raw_network_before_enqueue() -> None:
    store = SyncStore()
    executor = CloudSandboxExecutor(store=store, poll_seconds=0, wait_grace_seconds=1)

    with pytest.raises(SecretEgressBlockedError) as captured:
        executor.execute(_request(network=SandboxNetworkPolicy.PUBLIC))

    assert captured.value.error_code == "SECRET_EGRESS_BLOCKED"
    assert store.enqueued == []


def test_cloud_executor_allows_core_owned_dependency_registry_egress() -> None:
    store = SyncStore()
    executor = CloudSandboxExecutor(store=store, poll_seconds=0, wait_grace_seconds=1)
    request = _request(
        network=SandboxNetworkPolicy.PUBLIC,
        purpose=SandboxPurpose.DEPENDENCY,
    )

    result = executor.execute(request)

    assert result.status is SandboxResultStatus.COMPLETED
    assert store.enqueued == [request]


def test_cloud_executor_health_comes_from_a_recent_matching_worker() -> None:
    healthy = CloudSandboxExecutor(
        store=SyncStore(healthy=True),
        poll_seconds=0,
        wait_grace_seconds=1,
    ).health()
    unavailable = CloudSandboxExecutor(
        store=SyncStore(healthy=False),
        poll_seconds=0,
        wait_grace_seconds=1,
    ).health()

    assert healthy.available is True
    assert healthy.executor == "cloud_oci_worker"
    assert unavailable.available is False
    assert unavailable.error_code == "SANDBOX_UNAVAILABLE"


def test_cloud_executor_replays_a_durable_result_while_worker_is_offline() -> None:
    request = _request()
    store = SyncStore(healthy=False)
    store.jobs[request.job_id] = _completed(
        ExecutionJob.create(tenant_id="tenant-a", request=request)
    )
    executor = CloudSandboxExecutor(store=store, poll_seconds=0, wait_grace_seconds=1)

    result = executor.execute(request)

    assert result.stdout == b"ok\n"
    assert store.enqueued == []


def test_cloud_executor_rejects_a_different_request_for_a_completed_job() -> None:
    request = _request()
    store = SyncStore(healthy=False)
    conflicting_request = replace(request, argv=("python3", "-c", "print('different')"))
    store.jobs[request.job_id] = _completed(
        ExecutionJob.create(tenant_id="tenant-a", request=conflicting_request)
    )
    executor = CloudSandboxExecutor(store=store, poll_seconds=0, wait_grace_seconds=1)

    with pytest.raises(IdempotencyConflictError):
        executor.execute(request)


def test_execution_worker_module_entrypoint_has_no_eager_import_warning() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "error",
            "-c",
            (
                "import asyncio, runpy; "
                "asyncio.run=lambda coro: coro.close(); "
                "runpy.run_module('fairy_cloud.workers.execution', run_name='__main__')"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_cloud_runner_result_requires_bound_identity_and_output_hashes() -> None:
    request = _request()
    values = _runner_result(request)

    result = decode_sandbox_result(_json_bytes(values), request)

    assert result.stdout == b"cloud-ok\n"
    assert result.executor == "cloud_oci_worker"

    with pytest.raises(WorkerFenceError):
        decode_sandbox_result(
            _json_bytes({**values, "lease_fence": request.lease_fence + 1}),
            request,
        )
    with pytest.raises(ValueError, match="stdout hash"):
        decode_sandbox_result(
            _json_bytes({**values, "stdout_sha256": "0" * 64}),
            request,
        )


def test_claim_statement_is_brokerless_skip_locked_and_recovery_aware() -> None:
    statement = build_claim_execution_statement(now=datetime.now(UTC))
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).upper()

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "QUEUED" in sql
    assert "CLAIMED" in sql
    assert "RESULT_RECORDED" in sql
    assert "RUNNING" not in sql


class AsyncStore:
    def __init__(self, claims: list[ExecutionClaim]) -> None:
        self.claims = list(claims)
        self.calls: list[str] = []
        self.cancel_requested = False
        self.fail_finalize_once = False
        self.fence_error = False

    async def claim_next(self, *, owner_id: str, lease_seconds: int):
        del owner_id, lease_seconds
        self.calls.append("claim")
        return self.claims.pop(0) if self.claims else None

    async def mark_spawned(self, claim: ExecutionClaim) -> None:
        self.calls.append("spawned")
        if self.fence_error:
            raise WorkerFenceError("stale execution fence")

    async def cancellation_requested(self, claim: ExecutionClaim) -> bool:
        del claim
        self.calls.append("cancel-check")
        return self.cancel_requested

    async def mark_cancelled(self, claim: ExecutionClaim) -> None:
        del claim
        self.calls.append("cancelled")

    async def record_result(self, claim: ExecutionClaim, result: SandboxResult) -> None:
        del claim, result
        self.calls.append("result")

    async def finalize_result(self, claim: ExecutionClaim) -> None:
        del claim
        self.calls.append("finalized")
        if self.fail_finalize_once:
            self.fail_finalize_once = False
            raise RuntimeError("crash after durable result")

    async def mark_interrupted(self, claim: ExecutionClaim) -> None:
        del claim
        self.calls.append("interrupted")

    async def reconcile_expired(self) -> int:
        self.calls.append("reconcile")
        return 0

    async def heartbeat(self, *, owner_id: str) -> None:
        del owner_id
        self.calls.append("heartbeat")


class Runner:
    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.requests: list[SandboxRequest] = []

    async def execute(
        self,
        request: SandboxRequest,
        cancellation_requested: Callable[[], Awaitable[bool]],
    ) -> SandboxResult:
        self.requests.append(request)
        await cancellation_requested()
        if self.failure is not None:
            raise self.failure
        now = datetime.now(UTC)
        return SandboxResult.create(
            request=request,
            executor="cloud_oci_worker",
            executor_version="1.1.0",
            status=SandboxResultStatus.COMPLETED,
            exit_code=0,
            stdout=b"ok\n",
            stderr=b"",
            output_truncated=False,
            started_at=now,
            finished_at=now,
        )


def _claim(
    job: ExecutionJob,
    *,
    action: ExecutionClaimAction = ExecutionClaimAction.EXECUTE,
    fence: int = 1,
) -> ExecutionClaim:
    return ExecutionClaim(
        job=replace(
            job,
            status=(
                ExecutionJobStatus.RESULT_RECORDED
                if action is ExecutionClaimAction.FINALIZE
                else ExecutionJobStatus.CLAIMED
            ),
            lease_owner="execution-a",
            lease_fence=fence,
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        ),
        action=action,
    )


@pytest.mark.asyncio
async def test_execution_worker_records_and_finalizes_one_fenced_result() -> None:
    request = _request()
    claim = _claim(ExecutionJob.create(tenant_id="tenant-a", request=request))
    store = AsyncStore([claim])
    runner = Runner()
    worker = ExecutionWorker(store=store, runner=runner, owner_id="execution-a")

    cycle = await worker.run_once()

    assert cycle.claimed == 1
    assert cycle.completed == 1
    assert runner.requests == [request]
    assert store.calls == [
        "reconcile",
        "claim",
        "cancel-check",
        "spawned",
        "cancel-check",
        "result",
        "finalized",
        "heartbeat",
    ]


@pytest.mark.asyncio
async def test_execution_worker_cancels_before_spawn_without_running_code() -> None:
    job = ExecutionJob.create(tenant_id="tenant-a", request=_request())
    store = AsyncStore([_claim(job)])
    store.cancel_requested = True
    runner = Runner()
    worker = ExecutionWorker(store=store, runner=runner, owner_id="execution-a")

    cycle = await worker.run_once()

    assert cycle.cancelled == 1
    assert runner.requests == []
    assert "spawned" not in store.calls
    assert "cancelled" in store.calls


@pytest.mark.asyncio
async def test_execution_worker_never_retries_after_spawn_failure() -> None:
    job = ExecutionJob.create(tenant_id="tenant-a", request=_request())
    store = AsyncStore([_claim(job)])
    runner = Runner(failure=RuntimeError("container lost after spawn"))
    worker = ExecutionWorker(store=store, runner=runner, owner_id="execution-a")

    cycle = await worker.run_once()

    assert cycle.interrupted == 1
    assert len(runner.requests) == 1
    assert store.calls.count("spawned") == 1
    assert store.calls.count("interrupted") == 1


@pytest.mark.asyncio
async def test_recorded_result_is_finalized_after_crash_without_reexecution() -> None:
    job = ExecutionJob.create(tenant_id="tenant-a", request=_request())
    execute_claim = _claim(job, fence=1)
    finalize_claim = _claim(_completed(job), action=ExecutionClaimAction.FINALIZE, fence=2)
    store = AsyncStore([execute_claim, finalize_claim])
    store.fail_finalize_once = True
    runner = Runner()
    worker = ExecutionWorker(store=store, runner=runner, owner_id="execution-a")

    first = await worker.run_once()
    second = await worker.run_once()

    assert first.failed == 1
    assert second.completed == 1
    assert len(runner.requests) == 1
    assert store.calls.count("result") == 1
    assert store.calls.count("finalized") == 2


@pytest.mark.asyncio
async def test_stale_worker_fence_never_spawns_or_records_a_result() -> None:
    job = ExecutionJob.create(tenant_id="tenant-a", request=_request())
    store = AsyncStore([_claim(job)])
    store.fence_error = True
    runner = Runner()
    worker = ExecutionWorker(store=store, runner=runner, owner_id="execution-a")

    cycle = await worker.run_once()

    assert cycle.failed == 1
    assert runner.requests == []
    assert "result" not in store.calls


@pytest.mark.asyncio
async def test_worker_rejects_public_network_even_if_queue_data_bypasses_api() -> None:
    job = ExecutionJob.create(
        tenant_id="tenant-a",
        request=_request(network=SandboxNetworkPolicy.PUBLIC),
    )
    store = AsyncStore([_claim(job)])
    runner = Runner()
    worker = ExecutionWorker(store=store, runner=runner, owner_id="execution-a")

    cycle = await worker.run_once()

    assert cycle.interrupted == 1
    assert runner.requests == []
    assert "spawned" not in store.calls
    assert "interrupted" in store.calls


@pytest.mark.asyncio
async def test_worker_allows_public_network_only_for_dependency_purpose() -> None:
    request = _request(
        network=SandboxNetworkPolicy.PUBLIC,
        purpose=SandboxPurpose.DEPENDENCY,
    )
    job = ExecutionJob.create(tenant_id="tenant-a", request=request)
    store = AsyncStore([_claim(job)])
    runner = Runner()
    worker = ExecutionWorker(store=store, runner=runner, owner_id="execution-a")

    cycle = await worker.run_once()

    assert cycle.completed == 1
    assert runner.requests == [request]


def _runner_result(request: SandboxRequest) -> dict[str, object]:
    stdout = b"cloud-ok\n"
    stderr = b""
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": 1,
        "executor": "cloud_oci_worker",
        "executor_version": "1.1.0",
        "job_id": str(request.job_id),
        "scope_digest": request.scope_digest,
        "workspace_generation": request.workspace_generation,
        "lease_fence": request.lease_fence,
        "status": "completed",
        "exit_code": 0,
        "stdout_base64": base64.b64encode(stdout).decode("ascii"),
        "stderr_base64": base64.b64encode(stderr).decode("ascii"),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "output_truncated": False,
        "started_at": now,
        "finished_at": now,
    }


def _json_bytes(values: dict[str, object]) -> bytes:
    return json.dumps(values, sort_keys=True).encode("ascii")
