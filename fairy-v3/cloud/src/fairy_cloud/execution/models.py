from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.sandbox.models import (
    SandboxNetworkPolicy,
    SandboxPurpose,
    SandboxRequest,
    SandboxResult,
    SandboxResultStatus,
)


class ExecutionJobStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    RESULT_RECORDED = "result_recorded"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    @property
    def terminal(self) -> bool:
        return self in {
            ExecutionJobStatus.SUCCEEDED,
            ExecutionJobStatus.FAILED,
            ExecutionJobStatus.TIMED_OUT,
            ExecutionJobStatus.CANCELLED,
            ExecutionJobStatus.INTERRUPTED,
        }


class ExecutionClaimAction(StrEnum):
    EXECUTE = "execute"
    FINALIZE = "finalize"


@dataclass(frozen=True, slots=True)
class ExecutionJob:
    tenant_id: str
    job_id: UUID
    command_run_id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    scope_digest: str
    request_fingerprint: str
    workspace_generation: int
    request_lease_fence: int
    argv: tuple[str, ...]
    cwd: str
    environment: Mapping[str, str]
    timeout_seconds: int
    output_limit_bytes: int
    network_policy: SandboxNetworkPolicy
    purpose: SandboxPurpose
    dependency_key: str | None
    dependency_manager: str | None
    workspace_archive: bytes
    archive_sha256: str
    status: ExecutionJobStatus
    cancel_requested: bool
    attempts: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    lease_fence: int
    spawned_at: datetime | None
    result_status: SandboxResultStatus | None
    executor: str | None
    executor_version: str | None
    exit_code: int | None
    stdout: bytes | None
    stderr: bytes | None
    output_truncated: bool | None
    started_at: datetime | None
    result_recorded_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        request: SandboxRequest,
        now: datetime | None = None,
    ) -> ExecutionJob:
        observed_at = (now or datetime.now(UTC)).astimezone(UTC)
        return cls(
            tenant_id=normalize_tenant_id(tenant_id),
            job_id=request.job_id,
            command_run_id=request.job_id,
            project_id=request.project_id,
            conversation_id=request.conversation_id,
            task_id=request.task_id,
            version_id=request.version_id,
            scope_digest=request.scope_digest,
            request_fingerprint=execution_request_fingerprint(request),
            workspace_generation=request.workspace_generation,
            request_lease_fence=request.lease_fence,
            argv=request.argv,
            cwd=request.cwd,
            environment=MappingProxyType(dict(request.environment)),
            timeout_seconds=request.timeout_seconds,
            output_limit_bytes=request.output_limit_bytes,
            network_policy=request.network_policy,
            purpose=request.purpose,
            dependency_key=request.dependency_key,
            dependency_manager=request.dependency_manager,
            workspace_archive=request.workspace_archive,
            archive_sha256=request.archive_sha256,
            status=ExecutionJobStatus.QUEUED,
            cancel_requested=False,
            attempts=0,
            lease_owner=None,
            lease_expires_at=None,
            lease_fence=0,
            spawned_at=None,
            result_status=None,
            executor=None,
            executor_version=None,
            exit_code=None,
            stdout=None,
            stderr=None,
            output_truncated=None,
            started_at=None,
            result_recorded_at=None,
            finished_at=None,
            created_at=observed_at,
            updated_at=observed_at,
        )

    def to_request(self) -> SandboxRequest:
        return SandboxRequest.create(
            job_id=self.job_id,
            project_id=self.project_id,
            conversation_id=self.conversation_id,
            task_id=self.task_id,
            version_id=self.version_id,
            scope_digest=self.scope_digest,
            workspace_generation=self.workspace_generation,
            lease_fence=self.request_lease_fence,
            argv=self.argv,
            cwd=self.cwd,
            environment=self.environment,
            timeout_seconds=self.timeout_seconds,
            output_limit_bytes=self.output_limit_bytes,
            network_policy=self.network_policy,
            workspace_archive=self.workspace_archive,
            purpose=self.purpose,
            dependency_key=self.dependency_key,
            dependency_manager=self.dependency_manager,
        )

    def to_result(self) -> SandboxResult:
        if (
            self.status is ExecutionJobStatus.INTERRUPTED
            or not self.status.terminal
            or self.result_status is None
            or self.executor is None
            or self.executor_version is None
            or self.stdout is None
            or self.stderr is None
            or self.output_truncated is None
            or self.started_at is None
            or self.finished_at is None
        ):
            raise ValueError("Execution job has no complete terminal result")
        return SandboxResult.create(
            request=self.to_request(),
            executor=self.executor,
            executor_version=self.executor_version,
            status=self.result_status,
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            output_truncated=self.output_truncated,
            started_at=self.started_at,
            finished_at=self.finished_at,
        )


@dataclass(frozen=True, slots=True)
class ExecutionClaim:
    job: ExecutionJob
    action: ExecutionClaimAction

    def __post_init__(self) -> None:
        if not self.job.lease_owner or self.job.lease_fence < 1:
            raise ValueError("Execution claim requires a fenced owner")
        expected = (
            ExecutionJobStatus.RESULT_RECORDED
            if self.action is ExecutionClaimAction.FINALIZE
            else ExecutionJobStatus.CLAIMED
        )
        if self.job.status is not expected:
            raise ValueError("Execution claim action does not match job status")


def execution_request_fingerprint(request: SandboxRequest) -> str:
    encoded = json.dumps(
        request.header(),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ExecutionClaim",
    "ExecutionClaimAction",
    "ExecutionJob",
    "ExecutionJobStatus",
    "execution_request_fingerprint",
]
