from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import ScopeContract

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Workspace:
    id: UUID
    project_id: UUID
    conversation_id: UUID
    task_id: UUID
    version_id: UUID
    project_root: Path
    editable_files: tuple[Path, ...]
    reference_files: tuple[Path, ...]
    constraints: tuple[str, ...]
    allowed_paths: tuple[Path, ...]
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID,
        conversation_id: UUID,
        task_id: UUID,
        version_id: UUID,
        project_root: Path,
        editable_files: tuple[Path, ...],
        reference_files: tuple[Path, ...],
        constraints: tuple[str, ...],
        allowed_paths: tuple[Path, ...],
    ) -> Workspace:
        return cls(
            id=new_id(),
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            version_id=version_id,
            project_root=project_root.resolve(strict=False),
            editable_files=editable_files,
            reference_files=reference_files,
            constraints=constraints,
            allowed_paths=tuple(path.resolve(strict=False) for path in allowed_paths),
            created_at=_now(),
        )


class ChangesetStatus(StrEnum):
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPLYING = "applying"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


_CHANGESET_TRANSITIONS: dict[ChangesetStatus, frozenset[ChangesetStatus]] = {
    ChangesetStatus.PROPOSED: frozenset({ChangesetStatus.AWAITING_APPROVAL}),
    ChangesetStatus.AWAITING_APPROVAL: frozenset(
        {ChangesetStatus.APPLYING, ChangesetStatus.REJECTED}
    ),
    ChangesetStatus.APPLYING: frozenset({ChangesetStatus.APPLIED, ChangesetStatus.FAILED}),
    ChangesetStatus.APPLIED: frozenset({ChangesetStatus.ROLLED_BACK}),
    ChangesetStatus.REJECTED: frozenset(),
    ChangesetStatus.FAILED: frozenset({ChangesetStatus.ROLLED_BACK}),
    ChangesetStatus.ROLLED_BACK: frozenset(),
}


@dataclass(slots=True)
class Changeset:
    id: UUID
    project_id: UUID
    conversation_id: UUID
    task_id: UUID
    version_id: UUID
    files: tuple[str, ...]
    patches: tuple[str, ...]
    reason: str
    risk_level: str
    idempotency_key: str
    status: ChangesetStatus = ChangesetStatus.PROPOSED
    approval_decision: ApprovalDecision = ApprovalDecision.PENDING
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID,
        conversation_id: UUID,
        task_id: UUID,
        version_id: UUID,
        files: tuple[str, ...],
        patches: tuple[str, ...],
        reason: str,
        risk_level: str,
        idempotency_key: str,
    ) -> Changeset:
        if not files or len(files) != len(patches):
            raise ValueError("changeset requires one patch per file")
        if not idempotency_key.strip():
            raise ValueError("changeset idempotency_key is required")
        return cls(
            id=new_id(),
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            version_id=version_id,
            files=files,
            patches=patches,
            reason=reason.strip(),
            risk_level=risk_level,
            idempotency_key=idempotency_key.strip(),
        )

    def record_approval(self, decision: ApprovalDecision) -> None:
        if self.status is not ChangesetStatus.AWAITING_APPROVAL:
            raise InvalidTransitionError("changeset approval is not currently pending")
        if decision is ApprovalDecision.PENDING:
            raise ValueError("approval decision must be terminal")
        self.approval_decision = decision
        self.updated_at = _now()

    def transition_to(self, status: ChangesetStatus) -> None:
        if status not in _CHANGESET_TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"cannot transition Changeset from {self.status} to {status}"
            )
        if (
            status is ChangesetStatus.APPLYING
            and self.approval_decision is not ApprovalDecision.APPROVED
        ):
            raise InvalidTransitionError("approved Changeset required before applying")
        self.status = status
        self.updated_at = _now()


@dataclass(slots=True)
class Approval:
    id: UUID
    task_id: UUID
    command_run_id: UUID
    requested_by: str
    reason: str
    changeset_id: UUID | None = None
    decision: ApprovalDecision = ApprovalDecision.PENDING
    decided_by: str | None = None
    created_at: datetime = field(default_factory=_now)
    decided_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        task_id: UUID,
        command_run_id: UUID,
        requested_by: str,
        reason: str,
        changeset_id: UUID | None = None,
    ) -> Approval:
        return cls(
            id=new_id(),
            task_id=task_id,
            command_run_id=command_run_id,
            requested_by=requested_by,
            reason=reason,
            changeset_id=changeset_id,
        )

    def decide(self, *, decision: ApprovalDecision, decided_by: str) -> None:
        if self.decision is not ApprovalDecision.PENDING:
            raise InvalidTransitionError("approval has already been decided")
        if decision is ApprovalDecision.PENDING:
            raise ValueError("approval decision must be terminal")
        self.decision = decision
        self.decided_by = decided_by
        self.decided_at = _now()


class RuntimeKind(StrEnum):
    STATIC_SITE = "static_site"
    WSL_PROJECT = "wsl_project"
    CLOUD_OCI = "cloud_oci"


class RuntimeStatus(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class RuntimeHealth(StrEnum):
    UNKNOWN = "unknown"
    STARTING = "starting"
    HEALTHY = "healthy"
    STOPPING = "stopping"
    STOPPED = "stopped"
    UNHEALTHY = "unhealthy"
    INTERRUPTED = "interrupted"


class PreviewStatus(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class PreviewHealth(StrEnum):
    UNKNOWN = "unknown"
    STARTING = "starting"
    HEALTHY = "healthy"
    STOPPING = "stopping"
    STOPPED = "stopped"
    UNHEALTHY = "unhealthy"
    INTERRUPTED = "interrupted"


class PreviewVisibility(StrEnum):
    CHAT_DRAFT = "chat_draft"
    PROJECT_ACTIVE = "project_active"
    PRIVATE = "private"


_RUNTIME_TRANSITIONS: dict[RuntimeStatus, frozenset[RuntimeStatus]] = {
    RuntimeStatus.CREATED: frozenset({RuntimeStatus.STARTING, RuntimeStatus.INTERRUPTED}),
    RuntimeStatus.STARTING: frozenset(
        {RuntimeStatus.RUNNING, RuntimeStatus.FAILED, RuntimeStatus.INTERRUPTED}
    ),
    RuntimeStatus.RUNNING: frozenset(
        {RuntimeStatus.STOPPING, RuntimeStatus.FAILED, RuntimeStatus.INTERRUPTED}
    ),
    RuntimeStatus.STOPPING: frozenset(
        {RuntimeStatus.STOPPED, RuntimeStatus.FAILED, RuntimeStatus.INTERRUPTED}
    ),
    RuntimeStatus.STOPPED: frozenset(),
    RuntimeStatus.FAILED: frozenset({RuntimeStatus.STARTING}),
    RuntimeStatus.INTERRUPTED: frozenset(
        {RuntimeStatus.STARTING, RuntimeStatus.STOPPING, RuntimeStatus.FAILED}
    ),
}

_PREVIEW_TRANSITIONS: dict[PreviewStatus, frozenset[PreviewStatus]] = {
    PreviewStatus.CREATED: frozenset({PreviewStatus.STARTING, PreviewStatus.INTERRUPTED}),
    PreviewStatus.STARTING: frozenset(
        {PreviewStatus.READY, PreviewStatus.FAILED, PreviewStatus.INTERRUPTED}
    ),
    PreviewStatus.READY: frozenset(
        {PreviewStatus.STOPPING, PreviewStatus.FAILED, PreviewStatus.INTERRUPTED}
    ),
    PreviewStatus.STOPPING: frozenset(
        {PreviewStatus.STOPPED, PreviewStatus.FAILED, PreviewStatus.INTERRUPTED}
    ),
    PreviewStatus.STOPPED: frozenset(),
    PreviewStatus.FAILED: frozenset({PreviewStatus.STARTING}),
    PreviewStatus.INTERRUPTED: frozenset(
        {PreviewStatus.STARTING, PreviewStatus.STOPPING, PreviewStatus.FAILED}
    ),
}


@dataclass(frozen=True, slots=True)
class RuntimeSession:
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    project_root: Path
    execution_target: str
    kind: RuntimeKind
    executor: str
    executor_handle: str | None
    port: int | None
    status: RuntimeStatus
    health: RuntimeHealth
    error_code: str | None
    idempotency_key: str
    revision: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", Path(self.project_root).resolve(strict=False))
        object.__setattr__(self, "executor", _required_text(self.executor, "executor"))
        object.__setattr__(
            self,
            "idempotency_key",
            _required_text(self.idempotency_key, "idempotency_key"),
        )
        if self.execution_target not in {"local", "cloud"}:
            raise ValueError("execution_target must be local or cloud")
        if self.kind is RuntimeKind.STATIC_SITE and (
            self.execution_target != "local" or self.project_id is None or self.version_id is None
        ):
            raise ValueError("static_site requires a local Project Version")
        if self.revision < 0:
            raise ValueError("revision cannot be negative")
        _validate_timestamps(self.created_at, self.updated_at)
        _validate_handle_port(self.executor_handle, self.port)
        expected_health = {
            RuntimeStatus.CREATED: RuntimeHealth.UNKNOWN,
            RuntimeStatus.STARTING: RuntimeHealth.STARTING,
            RuntimeStatus.RUNNING: RuntimeHealth.HEALTHY,
            RuntimeStatus.STOPPING: RuntimeHealth.STOPPING,
            RuntimeStatus.STOPPED: RuntimeHealth.STOPPED,
            RuntimeStatus.FAILED: RuntimeHealth.UNHEALTHY,
            RuntimeStatus.INTERRUPTED: RuntimeHealth.INTERRUPTED,
        }[self.status]
        if self.health is not expected_health:
            raise ValueError("Runtime health is inconsistent with status")
        if self.status in {RuntimeStatus.RUNNING, RuntimeStatus.STOPPING} and (
            self.executor_handle is None or self.port is None
        ):
            raise ValueError("active Runtime requires executor handle and port")
        if self.status in {
            RuntimeStatus.CREATED,
            RuntimeStatus.STARTING,
            RuntimeStatus.STOPPED,
        } and (self.executor_handle is not None or self.port is not None):
            raise ValueError("inactive Runtime cannot retain executor handle or port")
        _validate_error_code(self.status, self.error_code)

    @classmethod
    def create(
        cls,
        *,
        scope: ScopeContract,
        kind: RuntimeKind,
        executor: str,
        idempotency_key: str,
    ) -> RuntimeSession:
        now = _now()
        return cls.restore(
            id=new_id(),
            project_id=scope.project_id,
            conversation_id=scope.conversation_id,
            task_id=scope.task_id,
            version_id=scope.target_version_id or scope.base_version_id,
            project_root=scope.project_root,
            execution_target=scope.execution_target,
            kind=kind,
            executor=executor,
            executor_handle=None,
            port=None,
            status=RuntimeStatus.CREATED,
            health=RuntimeHealth.UNKNOWN,
            error_code=None,
            idempotency_key=idempotency_key,
            revision=0,
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def restore(cls, **values: Any) -> RuntimeSession:
        return cls(**values)

    def begin_start(self) -> None:
        self._transition(RuntimeStatus.STARTING)
        object.__setattr__(self, "executor_handle", None)
        object.__setattr__(self, "port", None)
        object.__setattr__(self, "health", RuntimeHealth.STARTING)
        object.__setattr__(self, "error_code", None)

    def mark_running(self, *, executor_handle: str, port: int) -> None:
        handle = _required_text(executor_handle, "executor_handle")
        _validate_port(port)
        self._transition(RuntimeStatus.RUNNING)
        object.__setattr__(self, "executor_handle", handle)
        object.__setattr__(self, "port", port)
        object.__setattr__(self, "health", RuntimeHealth.HEALTHY)
        object.__setattr__(self, "error_code", None)

    def begin_stop(self) -> None:
        if self.executor_handle is None or self.port is None:
            raise InvalidTransitionError("Runtime cannot stop without an executor handle")
        self._transition(RuntimeStatus.STOPPING)
        object.__setattr__(self, "health", RuntimeHealth.STOPPING)
        object.__setattr__(self, "error_code", None)

    def mark_stopped(self) -> None:
        self._transition(RuntimeStatus.STOPPED)
        object.__setattr__(self, "executor_handle", None)
        object.__setattr__(self, "port", None)
        object.__setattr__(self, "health", RuntimeHealth.STOPPED)
        object.__setattr__(self, "error_code", None)

    def mark_failed(self, error_code: str) -> None:
        normalized_error = _required_text(error_code, "error_code")
        self._transition(RuntimeStatus.FAILED)
        object.__setattr__(self, "health", RuntimeHealth.UNHEALTHY)
        object.__setattr__(self, "error_code", normalized_error)

    def mark_interrupted(self, error_code: str = "WORKER_INTERRUPTED") -> None:
        normalized_error = _required_text(error_code, "error_code")
        self._transition(RuntimeStatus.INTERRUPTED)
        object.__setattr__(self, "health", RuntimeHealth.INTERRUPTED)
        object.__setattr__(self, "error_code", normalized_error)

    def _transition(self, target: RuntimeStatus) -> None:
        if target not in _RUNTIME_TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"cannot transition Runtime from {self.status} to {target}"
            )
        object.__setattr__(self, "status", target)
        object.__setattr__(self, "revision", self.revision + 1)
        object.__setattr__(self, "updated_at", _now())


@dataclass(frozen=True, slots=True)
class PreviewSession:
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    runtime_id: UUID
    project_root: Path
    execution_target: str
    url: str | None
    visibility: PreviewVisibility
    status: PreviewStatus
    health: PreviewHealth
    error_code: str | None
    idempotency_key: str
    revision: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", Path(self.project_root).resolve(strict=False))
        object.__setattr__(
            self,
            "idempotency_key",
            _required_text(self.idempotency_key, "idempotency_key"),
        )
        if self.execution_target not in {"local", "cloud"}:
            raise ValueError("execution_target must be local or cloud")
        if self.revision < 0:
            raise ValueError("revision cannot be negative")
        _validate_timestamps(self.created_at, self.updated_at)
        expected_health = {
            PreviewStatus.CREATED: PreviewHealth.UNKNOWN,
            PreviewStatus.STARTING: PreviewHealth.STARTING,
            PreviewStatus.READY: PreviewHealth.HEALTHY,
            PreviewStatus.STOPPING: PreviewHealth.STOPPING,
            PreviewStatus.STOPPED: PreviewHealth.STOPPED,
            PreviewStatus.FAILED: PreviewHealth.UNHEALTHY,
            PreviewStatus.INTERRUPTED: PreviewHealth.INTERRUPTED,
        }[self.status]
        if self.health is not expected_health:
            raise ValueError("Preview health is inconsistent with status")
        if self.status in {PreviewStatus.READY, PreviewStatus.STOPPING}:
            if self.url is None:
                raise ValueError("active Preview requires a URL")
            _validate_preview_url(self.url, execution_target=self.execution_target)
        elif self.status is not PreviewStatus.INTERRUPTED and self.url is not None:
            raise ValueError("inactive Preview cannot retain a URL")
        if self.status is PreviewStatus.INTERRUPTED and self.url is not None:
            _validate_preview_url(self.url, execution_target=self.execution_target)
        _validate_error_code(self.status, self.error_code)

    @classmethod
    def create(
        cls,
        *,
        scope: ScopeContract,
        runtime_id: UUID,
        visibility: PreviewVisibility,
        idempotency_key: str,
    ) -> PreviewSession:
        now = _now()
        return cls.restore(
            id=new_id(),
            project_id=scope.project_id,
            conversation_id=scope.conversation_id,
            task_id=scope.task_id,
            version_id=scope.target_version_id or scope.base_version_id,
            runtime_id=runtime_id,
            project_root=scope.project_root,
            execution_target=scope.execution_target,
            url=None,
            visibility=visibility,
            status=PreviewStatus.CREATED,
            health=PreviewHealth.UNKNOWN,
            error_code=None,
            idempotency_key=idempotency_key,
            revision=0,
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def restore(cls, **values: Any) -> PreviewSession:
        return cls(**values)

    def begin_start(self) -> None:
        self._transition(PreviewStatus.STARTING)
        object.__setattr__(self, "url", None)
        object.__setattr__(self, "health", PreviewHealth.STARTING)
        object.__setattr__(self, "error_code", None)

    def mark_ready(self, url: str) -> None:
        normalized = _validate_preview_url(url, execution_target=self.execution_target)
        self._transition(PreviewStatus.READY)
        object.__setattr__(self, "url", normalized)
        object.__setattr__(self, "health", PreviewHealth.HEALTHY)
        object.__setattr__(self, "error_code", None)

    def begin_stop(self) -> None:
        if self.url is None:
            raise InvalidTransitionError("Preview cannot stop without a URL")
        self._transition(PreviewStatus.STOPPING)
        object.__setattr__(self, "health", PreviewHealth.STOPPING)
        object.__setattr__(self, "error_code", None)

    def mark_stopped(self) -> None:
        self._transition(PreviewStatus.STOPPED)
        object.__setattr__(self, "url", None)
        object.__setattr__(self, "health", PreviewHealth.STOPPED)
        object.__setattr__(self, "error_code", None)

    def mark_failed(self, error_code: str) -> None:
        normalized_error = _required_text(error_code, "error_code")
        self._transition(PreviewStatus.FAILED)
        object.__setattr__(self, "url", None)
        object.__setattr__(self, "health", PreviewHealth.UNHEALTHY)
        object.__setattr__(self, "error_code", normalized_error)

    def mark_interrupted(self, error_code: str = "WORKER_INTERRUPTED") -> None:
        normalized_error = _required_text(error_code, "error_code")
        self._transition(PreviewStatus.INTERRUPTED)
        object.__setattr__(self, "health", PreviewHealth.INTERRUPTED)
        object.__setattr__(self, "error_code", normalized_error)

    def _transition(self, target: PreviewStatus) -> None:
        if target not in _PREVIEW_TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"cannot transition Preview from {self.status} to {target}"
            )
        object.__setattr__(self, "status", target)
        object.__setattr__(self, "revision", self.revision + 1)
        object.__setattr__(self, "updated_at", _now())


class ArtifactVisibility(StrEnum):
    CONVERSATION = "conversation"
    PROJECT = "project"
    PRIVATE = "private"


class ArtifactType(StrEnum):
    DOCUMENT = "document"
    PROMPT = "prompt"
    PATCH = "patch"
    COMPONENT = "component"
    FULL_PROJECT = "full_project"
    PREVIEW_MANIFEST = "preview_manifest"
    PREVIEW_SNAPSHOT = "preview_snapshot"
    REPORT = "report"
    LOG = "log"
    SCREENSHOT = "screenshot"


@dataclass(frozen=True, slots=True)
class Artifact:
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    artifact_type: ArtifactType
    visibility: ArtifactVisibility
    storage_location: str
    media_type: str
    byte_length: int
    content_hash: str
    metadata: Mapping[str, Any]
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID | None,
        conversation_id: UUID,
        task_id: UUID,
        version_id: UUID | None,
        artifact_type: ArtifactType,
        visibility: ArtifactVisibility,
        storage_location: str,
        media_type: str,
        byte_length: int,
        content_hash: str,
        metadata: Mapping[str, Any],
    ) -> Artifact:
        location = _required_text(storage_location, "storage_location")
        normalized_media_type = _required_text(media_type, "media_type")
        if "/" not in normalized_media_type:
            raise ValueError("media_type must be a valid type/subtype")
        if byte_length < 0:
            raise ValueError("byte_length cannot be negative")
        if _SHA256_PATTERN.fullmatch(content_hash) is None:
            raise ValueError("content_hash must be a lowercase SHA-256 hex digest")
        try:
            normalized_metadata = json.loads(
                json.dumps(
                    dict(metadata),
                    ensure_ascii=True,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        except (TypeError, ValueError) as error:
            raise ValueError("metadata must be JSON-compatible") from error
        return cls(
            id=new_id(),
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            version_id=version_id,
            artifact_type=artifact_type,
            visibility=visibility,
            storage_location=location,
            media_type=normalized_media_type,
            byte_length=byte_length,
            content_hash=content_hash,
            metadata=_freeze_json(normalized_metadata),
            created_at=_now(),
        )


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _validate_port(port: int) -> None:
    if isinstance(port, bool) or not 1 <= port <= 65_535:
        raise ValueError("port must be between 1 and 65535")


def _validate_handle_port(handle: str | None, port: int | None) -> None:
    if (handle is None) != (port is None):
        raise ValueError("executor_handle and port must be both present")
    if handle is not None:
        _required_text(handle, "executor_handle")
        assert port is not None
        _validate_port(port)


def _validate_timestamps(created_at: datetime, updated_at: datetime) -> None:
    for field_name, value in (("created_at", created_at), ("updated_at", updated_at)):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
    if updated_at < created_at:
        raise ValueError("updated_at cannot precede created_at")


def _validate_error_code(
    status: RuntimeStatus | PreviewStatus,
    error_code: str | None,
) -> None:
    failed = status in {
        RuntimeStatus.FAILED,
        RuntimeStatus.INTERRUPTED,
        PreviewStatus.FAILED,
        PreviewStatus.INTERRUPTED,
    }
    if failed:
        if error_code is None:
            raise ValueError("failed or interrupted state requires an error_code")
        _required_text(error_code, "error_code")
    elif error_code is not None:
        raise ValueError("non-error state cannot carry an error_code")


def _validate_preview_url(url: str, *, execution_target: str) -> str:
    normalized = _required_text(url, "url")
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as error:
        raise ValueError("Preview URL is invalid") from error
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Preview URL cannot include credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Preview URL cannot include query or fragment")
    if not parsed.path.startswith("/"):
        raise ValueError("Preview URL requires an absolute path")
    if execution_target == "local":
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or port is None:
            raise ValueError("local Preview URL must use loopback HTTP with an explicit port")
        _validate_port(port)
    elif parsed.scheme != "https" or parsed.hostname is None:
        raise ValueError("cloud Preview URL must use HTTPS")
    return normalized


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class Checkpoint:
    id: UUID
    task_id: UUID
    version_id: UUID
    changed_files: tuple[str, ...]
    command_run_ids: tuple[UUID, ...]
    preview_artifact_id: UUID | None
    created_at: datetime

    @classmethod
    def create(cls, **values: Any) -> Checkpoint:
        return cls(id=new_id(), created_at=_now(), **values)


class MemoryScope(StrEnum):
    PROJECT_CANONICAL = "project_canonical"
    CONVERSATION_DRAFT = "conversation_draft"
    FAILURE_LESSON = "failure_lesson"
    PERSONAL = "personal"


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    id: UUID
    project_id: UUID | None
    conversation_id: UUID | None
    version_id: UUID | None
    scope: MemoryScope
    content: str
    created_at: datetime

    @classmethod
    def create(cls, **values: Any) -> MemoryEntry:
        content = str(values.pop("content")).strip()
        if not content:
            raise ValueError("memory content is required")
        return cls(id=new_id(), content=content, created_at=_now(), **values)
