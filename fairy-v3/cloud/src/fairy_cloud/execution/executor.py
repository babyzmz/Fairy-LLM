from __future__ import annotations

import time
from typing import Protocol
from uuid import UUID

from fairy_core.domain.errors import IdempotencyConflictError
from fairy_core.runtime.models import RuntimeExecutorHealth
from fairy_core.sandbox.models import SandboxNetworkPolicy, SandboxRequest, SandboxResult

from fairy_cloud.execution.models import (
    ExecutionJob,
    ExecutionJobStatus,
    execution_request_fingerprint,
)


class SecretEgressBlockedError(RuntimeError):
    error_code = "SECRET_EGRESS_BLOCKED"
    code = error_code


class WorkerInterruptedError(RuntimeError):
    error_code = "WORKER_INTERRUPTED"
    code = error_code


class ExecutionStore(Protocol):
    def health(self) -> RuntimeExecutorHealth: ...

    def enqueue(self, request: SandboxRequest) -> ExecutionJob: ...

    def get(self, job_id: UUID) -> ExecutionJob | None: ...

    def request_cancel(self, job_id: UUID) -> None: ...


class CloudSandboxExecutor:
    """Synchronous Core adapter over the durable PostgreSQL execution queue."""

    def __init__(
        self,
        *,
        store: ExecutionStore,
        poll_seconds: float = 0.05,
        wait_grace_seconds: int = 15,
    ) -> None:
        if poll_seconds < 0:
            raise ValueError("poll_seconds cannot be negative")
        if wait_grace_seconds < 1:
            raise ValueError("wait_grace_seconds must be positive")
        self._store = store
        self._poll_seconds = poll_seconds
        self._wait_grace_seconds = wait_grace_seconds

    def health(self) -> RuntimeExecutorHealth:
        try:
            return self._store.health()
        except Exception:
            return RuntimeExecutorHealth(
                available=False,
                executor="cloud_oci_worker",
                version=None,
                error_code="SANDBOX_UNAVAILABLE",
                diagnostics=("Cloud execution worker health could not be read",),
            )

    def execute(self, request: SandboxRequest) -> SandboxResult:
        if request.network_policy is SandboxNetworkPolicy.PUBLIC:
            raise SecretEgressBlockedError(
                "SECRET_EGRESS_BLOCKED: raw cloud Sandbox network is disabled"
            )
        job = self._store.get(request.job_id)
        if job is not None:
            self._validate_request_binding(job, request)
            terminal = self._terminal_result(job)
            if terminal is not None:
                return terminal

        health = self.health()
        if (
            not health.available
            or health.executor != "cloud_oci_worker"
            or health.version != "1.0.0"
        ):
            raise WorkerInterruptedError(
                "WORKER_INTERRUPTED: no attested Cloud OCI execution worker is ready"
            )

        if job is None:
            job = self._store.enqueue(request)
        deadline = time.monotonic() + request.timeout_seconds + self._wait_grace_seconds
        while True:
            terminal = self._terminal_result(job)
            if terminal is not None:
                return terminal
            if time.monotonic() >= deadline:
                self._store.request_cancel(request.job_id)
                raise WorkerInterruptedError(
                    "WORKER_INTERRUPTED: execution result was not observed before its deadline"
                )
            if self._poll_seconds:
                time.sleep(self._poll_seconds)
            refreshed = self._store.get(request.job_id)
            if refreshed is None:
                raise WorkerInterruptedError(
                    "WORKER_INTERRUPTED: durable execution job disappeared"
                )
            job = refreshed

    def cancel(self, job_id: UUID) -> None:
        self._store.request_cancel(job_id)

    @staticmethod
    def _validate_request_binding(job: ExecutionJob, request: SandboxRequest) -> None:
        if job.request_fingerprint != execution_request_fingerprint(request):
            raise IdempotencyConflictError("Execution job is already bound to a different request")

    @staticmethod
    def _terminal_result(job: ExecutionJob) -> SandboxResult | None:
        if job.status is ExecutionJobStatus.INTERRUPTED:
            raise WorkerInterruptedError(
                "WORKER_INTERRUPTED: execution stopped after process spawn"
            )
        if not job.status.terminal:
            return None
        try:
            return job.to_result()
        except ValueError as error:
            raise WorkerInterruptedError(
                "WORKER_INTERRUPTED: execution ended without a durable worker result"
            ) from error


__all__ = [
    "CloudSandboxExecutor",
    "ExecutionStore",
    "SecretEgressBlockedError",
    "WorkerInterruptedError",
]
