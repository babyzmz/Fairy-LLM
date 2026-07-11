from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from fairy_core.domain.execution import RuntimeKind
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.runtime.models import DynamicRuntimeStart

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class CloudRuntimeStatus(StrEnum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

    @property
    def terminal(self) -> bool:
        return self in {
            CloudRuntimeStatus.STOPPED,
            CloudRuntimeStatus.FAILED,
            CloudRuntimeStatus.INTERRUPTED,
        }


class CloudRuntimeClaimAction(StrEnum):
    START = "start"
    RECOVER = "recover"
    STOP = "stop"


@dataclass(frozen=True, slots=True)
class CloudRuntimeLease:
    tenant_id: str
    runtime_id: UUID
    preview_id: UUID
    project_id: UUID
    conversation_id: UUID
    task_id: UUID
    version_id: UUID
    scope_digest: str
    request_fingerprint: str
    workspace_generation: int
    request_lease_fence: int
    adapter: str
    argv: tuple[str, ...]
    cwd: str
    readiness_path: str
    startup_timeout_seconds: int
    dependency_key: str
    workspace_archive: bytes
    archive_sha256: str
    status: CloudRuntimeStatus
    internal_url: str | None
    worker_id: str | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    lease_fence: int
    attempts: int
    expires_at: datetime
    error_code: str | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", normalize_tenant_id(self.tenant_id))
        object.__setattr__(self, "argv", tuple(self.argv))
        object.__setattr__(self, "workspace_archive", bytes(self.workspace_archive))
        for name in ("scope_digest", "request_fingerprint", "dependency_key", "archive_sha256"):
            if _DIGEST.fullmatch(str(getattr(self, name))) is None:
                raise ValueError(f"Cloud Runtime {name} is invalid")
        if self.workspace_generation < 1 or self.request_lease_fence < 1:
            raise ValueError("Cloud Runtime request fence is invalid")
        if self.lease_fence < 0:
            raise ValueError("Cloud Runtime worker fence is invalid")
        if self.attempts < 0:
            raise ValueError("Cloud Runtime attempts are invalid")
        if self.expires_at.tzinfo is None or self.created_at.tzinfo is None:
            raise ValueError("Cloud Runtime timestamps require a timezone")
        if self.updated_at.tzinfo is None or self.expires_at <= self.created_at:
            raise ValueError("Cloud Runtime timestamps are invalid")
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("Cloud Runtime worker lease fields must be present together")
        if self.lease_expires_at is not None and self.lease_expires_at.tzinfo is None:
            raise ValueError("Cloud Runtime worker lease expiry requires a timezone")
        if self.status is CloudRuntimeStatus.RUNNING:
            if not self.internal_url or not self.worker_id or self.lease_fence < 1:
                raise ValueError("running Cloud Runtime requires a fenced internal endpoint")
            parsed = urlsplit(self.internal_url)
            if (
                parsed.scheme != "http"
                or parsed.hostname != "runtime"
                or parsed.port != 8082
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.path != f"/internal/{self.runtime_id}/"
            ):
                raise ValueError("Cloud Runtime internal endpoint is invalid")
        if self.status in {CloudRuntimeStatus.QUEUED, CloudRuntimeStatus.STOPPED} and (
            self.internal_url is not None
        ):
            raise ValueError("inactive Cloud Runtime cannot expose an internal endpoint")
        if self.status in {CloudRuntimeStatus.FAILED, CloudRuntimeStatus.INTERRUPTED}:
            if not self.error_code:
                raise ValueError("failed Cloud Runtime requires an error code")
        elif self.error_code is not None:
            raise ValueError("healthy Cloud Runtime cannot carry an error code")

    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        request: DynamicRuntimeStart,
        status: CloudRuntimeStatus = CloudRuntimeStatus.QUEUED,
        internal_url: str | None = None,
        worker_id: str | None = None,
        lease_owner: str | None = None,
        lease_expires_at: datetime | None = None,
        lease_fence: int = 0,
        attempts: int = 0,
        expires_at: datetime,
        error_code: str | None = None,
        now: datetime | None = None,
    ) -> CloudRuntimeLease:
        observed_at = (now or datetime.now(UTC)).astimezone(UTC)
        return cls(
            tenant_id=tenant_id,
            runtime_id=request.runtime_id,
            preview_id=request.preview_id,
            project_id=request.project_id,
            conversation_id=request.conversation_id,
            task_id=request.task_id,
            version_id=request.version_id,
            scope_digest=request.scope_digest,
            request_fingerprint=cloud_runtime_fingerprint(request),
            workspace_generation=request.workspace_generation,
            request_lease_fence=request.lease_fence,
            adapter=request.adapter,
            argv=request.argv,
            cwd=request.cwd,
            readiness_path=request.readiness_path,
            startup_timeout_seconds=request.startup_timeout_seconds,
            dependency_key=request.dependency_key,
            workspace_archive=request.workspace_archive,
            archive_sha256=request.archive_sha256,
            status=status,
            internal_url=internal_url,
            worker_id=worker_id,
            lease_owner=lease_owner,
            lease_expires_at=(
                lease_expires_at.astimezone(UTC) if lease_expires_at is not None else None
            ),
            lease_fence=lease_fence,
            attempts=attempts,
            expires_at=expires_at.astimezone(UTC),
            error_code=error_code,
            created_at=observed_at,
            updated_at=observed_at,
        )

    def matches(self, request: DynamicRuntimeStart) -> bool:
        return (
            self.runtime_id == request.runtime_id
            and self.preview_id == request.preview_id
            and self.project_id == request.project_id
            and self.conversation_id == request.conversation_id
            and self.task_id == request.task_id
            and self.version_id == request.version_id
            and self.scope_digest == request.scope_digest
            and self.workspace_generation == request.workspace_generation
            and self.request_lease_fence == request.lease_fence
            and self.request_fingerprint == cloud_runtime_fingerprint(request)
        )

    def to_request(self) -> DynamicRuntimeStart:
        return DynamicRuntimeStart(
            project_id=self.project_id,
            conversation_id=self.conversation_id,
            task_id=self.task_id,
            version_id=self.version_id,
            runtime_id=self.runtime_id,
            preview_id=self.preview_id,
            project_root=Path("/cloud-runtime"),
            execution_target="cloud",
            kind=RuntimeKind.CLOUD_OCI,
            adapter=self.adapter,
            scope_digest=self.scope_digest,
            workspace_generation=self.workspace_generation,
            lease_fence=self.request_lease_fence,
            argv=self.argv,
            cwd=self.cwd,
            readiness_path=self.readiness_path,
            startup_timeout_seconds=self.startup_timeout_seconds,
            dependency_key=self.dependency_key,
            workspace_archive=self.workspace_archive,
            archive_sha256=self.archive_sha256,
        )


@dataclass(frozen=True, slots=True)
class CloudRuntimeClaim:
    lease: CloudRuntimeLease
    action: CloudRuntimeClaimAction

    def __post_init__(self) -> None:
        if (
            not self.lease.lease_owner
            or self.lease.lease_expires_at is None
            or self.lease.lease_fence < 1
        ):
            raise ValueError("Cloud Runtime claim requires a fenced worker lease")


@dataclass(frozen=True, slots=True)
class CloudRuntimeReviewBinding:
    runtime_id: UUID
    preview_id: UUID
    project_id: UUID
    conversation_id: UUID
    task_id: UUID
    version_id: UUID
    scope_digest: str
    workspace_generation: int

    def __post_init__(self) -> None:
        if _DIGEST.fullmatch(self.scope_digest) is None:
            raise ValueError("Cloud Runtime Review Scope digest is invalid")
        if self.workspace_generation < 1:
            raise ValueError("Cloud Runtime Review generation is invalid")


def cloud_runtime_fingerprint(request: DynamicRuntimeStart) -> str:
    values = {
        "project_id": str(request.project_id),
        "conversation_id": str(request.conversation_id),
        "task_id": str(request.task_id),
        "version_id": str(request.version_id),
        "runtime_id": str(request.runtime_id),
        "preview_id": str(request.preview_id),
        "execution_target": request.execution_target,
        "kind": request.kind.value,
        "adapter": request.adapter,
        "scope_digest": request.scope_digest,
        "workspace_generation": request.workspace_generation,
        "request_lease_fence": request.lease_fence,
        "argv": list(request.argv),
        "cwd": request.cwd,
        "readiness_path": request.readiness_path,
        "startup_timeout_seconds": request.startup_timeout_seconds,
        "dependency_key": request.dependency_key,
        "archive_sha256": request.archive_sha256,
    }
    encoded = json.dumps(
        values,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "CloudRuntimeClaim",
    "CloudRuntimeClaimAction",
    "CloudRuntimeLease",
    "CloudRuntimeReviewBinding",
    "CloudRuntimeStatus",
    "cloud_runtime_fingerprint",
]
