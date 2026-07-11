from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

JsonValue = str | int | float | bool | None | list[Any] | dict[str, Any]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
        use_enum_values=False,
    )


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


class PublicMessageVisibilityModel(StrEnum):
    USER = "user"
    DEVELOPER = "developer"


class ErrorCode(StrEnum):
    PATH_OUT_OF_SCOPE = "PATH_OUT_OF_SCOPE"
    PATH_IDENTITY_CHANGED = "PATH_IDENTITY_CHANGED"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    SANDBOX_UNAVAILABLE = "SANDBOX_UNAVAILABLE"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    SECRET_EGRESS_BLOCKED = "SECRET_EGRESS_BLOCKED"
    CAPABILITY_NOT_AVAILABLE = "CAPABILITY_NOT_AVAILABLE"
    WORKER_INTERRUPTED = "WORKER_INTERRUPTED"
    MEMORY_SCOPE_VIOLATION = "MEMORY_SCOPE_VIOLATION"
    MEMORY_CONFLICT = "MEMORY_CONFLICT"
    MEMORY_INJECTION_BLOCKED = "MEMORY_INJECTION_BLOCKED"
    MEMORY_SECRET_BLOCKED = "MEMORY_SECRET_BLOCKED"
    MEMORY_PROJECTION_STALE = "MEMORY_PROJECTION_STALE"
    MEMORY_SNAPSHOT_TOO_LARGE = "MEMORY_SNAPSHOT_TOO_LARGE"
    MEMORY_FORGOTTEN = "MEMORY_FORGOTTEN"
    DOCUMENT_PROJECTION_STALE = "DOCUMENT_PROJECTION_STALE"
    DOCUMENT_INTEGRITY_FAILED = "DOCUMENT_INTEGRITY_FAILED"


__all__ = [
    "ContractModel",
    "ErrorCode",
    "EventVisibilityModel",
    "ExecutionTarget",
    "JsonValue",
    "PermissionProfileModel",
    "PublicMessageVisibilityModel",
]
