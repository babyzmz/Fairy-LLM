from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fairy_core.domain.models import OperationMode


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)


class ExecutionTarget(StrEnum):
    LOCAL = "local"
    CLOUD = "cloud"


class PermissionProfileModel(StrEnum):
    OBSERVE = "observe"
    STANDARD = "standard"
    AUTONOMOUS = "autonomous"


class EventVisibilityModel(StrEnum):
    USER = "user"
    DEVELOPER = "developer"
    INTERNAL = "internal"


class ErrorCode(StrEnum):
    PATH_OUT_OF_SCOPE = "PATH_OUT_OF_SCOPE"
    PATH_IDENTITY_CHANGED = "PATH_IDENTITY_CHANGED"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    SANDBOX_UNAVAILABLE = "SANDBOX_UNAVAILABLE"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    SECRET_EGRESS_BLOCKED = "SECRET_EGRESS_BLOCKED"
    CAPABILITY_NOT_AVAILABLE = "CAPABILITY_NOT_AVAILABLE"
    WORKER_INTERRUPTED = "WORKER_INTERRUPTED"


class TaskCreate(ContractModel):
    conversation_id: UUID
    user_request: str = Field(min_length=1, max_length=100_000)
    operation_mode: OperationMode
    execution_target: ExecutionTarget
    idempotency_key: str = Field(min_length=1, max_length=255)


class FileMutation(ContractModel):
    path: str = Field(min_length=1, max_length=1_024)
    content: str = Field(max_length=5_000_000)

    @field_validator("path")
    @classmethod
    def require_project_relative_path(cls, value: str) -> str:
        normalized = value.strip()
        parts = normalized.split("/")
        if (
            not normalized
            or normalized.startswith(("/", "\\"))
            or "\\" in normalized
            or ":" in normalized
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError("path must be a normalized project-relative path")
        return normalized


class ChangesetProposal(ContractModel):
    task_id: UUID
    files: tuple[FileMutation, ...] = Field(min_length=1, max_length=1_000)
    reason: str = Field(min_length=1, max_length=10_000)
    idempotency_key: str = Field(min_length=1, max_length=255)


class EventEnvelopeModel(ContractModel):
    id: UUID
    cursor: int = Field(ge=1)
    run_id: UUID | None
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    task_sequence: int = Field(ge=1)
    event_type: str = Field(min_length=1)
    visibility: EventVisibilityModel
    message: str
    payload: dict[str, Any]
    schema_version: int = Field(ge=1)
    created_at: datetime


class ErrorModel(ContractModel):
    code: ErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class CapabilityManifestModel(ContractModel):
    profile: PermissionProfileModel
    operations: dict[str, bool]
    sandbox_healthy: bool
    schema_version: int = 1
