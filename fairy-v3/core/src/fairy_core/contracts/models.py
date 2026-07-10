from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fairy_core.commanding.types import PermissionProfile
from fairy_core.domain.execution import ApprovalDecision, ChangesetStatus
from fairy_core.domain.models import (
    OperationMode,
    ProjectResidency,
    TaskStatus,
    VersionVisibility,
    WorkspaceType,
)


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


class TaskCreate(ContractModel):
    conversation_id: UUID
    user_request: str = Field(min_length=1, max_length=100_000)
    operation_mode: OperationMode
    execution_target: ExecutionTarget
    idempotency_key: str = Field(min_length=1, max_length=255)


class ProjectCreate(ContractModel):
    name: str = Field(min_length=1, max_length=255)
    residency: ProjectResidency


class ProjectImport(ProjectCreate):
    source_path: Path


class ConversationCreate(ContractModel):
    project_id: UUID | None
    workspace_type: WorkspaceType


class ApprovalDecisionInput(ContractModel):
    approval_id: UUID
    approved: bool
    decided_by: str = Field(min_length=1, max_length=255)


class TaskIdInput(ContractModel):
    task_id: UUID


class ProjectIdInput(ContractModel):
    project_id: UUID


class VersionIdInput(ContractModel):
    version_id: UUID


class VersionAcceptInput(TaskIdInput):
    expected_project_revision: int = Field(ge=0)
    user_confirmed: bool


class CapabilityRequest(ContractModel):
    profile: PermissionProfile = PermissionProfile.STANDARD
    sandbox_healthy: bool = False
    overrides: dict[str, bool] = Field(default_factory=dict)


class ProjectModel(ContractModel):
    id: UUID
    name: str
    residency: ProjectResidency
    active_version_id: UUID | None
    active_preview_id: UUID | None
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class ConversationModel(ContractModel):
    id: UUID
    project_id: UUID | None
    workspace_type: WorkspaceType
    base_version_id: UUID | None
    active_draft_version_id: UUID | None
    active_task_id: UUID | None
    active_preview_id: UUID | None
    created_at: datetime
    updated_at: datetime


class TaskModel(ContractModel):
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    user_request: str
    operation_mode: OperationMode
    base_version_id: UUID | None
    execution_target: ExecutionTarget
    target_version_id: UUID | None
    status: TaskStatus
    created_at: datetime
    updated_at: datetime


class VersionModel(ContractModel):
    id: UUID
    project_id: UUID
    source_conversation_id: UUID | None
    source_task_id: UUID | None
    parent_version_id: UUID | None
    project_root: Path
    visibility: VersionVisibility
    created_at: datetime


class ScopeContractModel(ContractModel):
    workspace_type: WorkspaceType
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    operation_mode: OperationMode
    base_version_id: UUID | None
    target_version_id: UUID | None
    project_root: Path
    allowed_write_paths: tuple[Path, ...]
    forbidden_write_paths: tuple[Path, ...]
    execution_target: ExecutionTarget
    network_policy: str
    memory_read_scope: tuple[str, ...]
    memory_write_scope: tuple[str, ...]
    scope_digest: str


class ChangesetModel(ContractModel):
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
    status: ChangesetStatus
    approval_decision: ApprovalDecision
    created_at: datetime
    updated_at: datetime


class ApprovalModel(ContractModel):
    id: UUID
    task_id: UUID
    command_run_id: UUID
    requested_by: str
    reason: str
    changeset_id: UUID | None
    decision: ApprovalDecision
    decided_by: str | None
    created_at: datetime
    decided_at: datetime | None


class CheckpointModel(ContractModel):
    id: UUID
    task_id: UUID
    version_id: UUID
    changed_files: tuple[str, ...]
    command_run_ids: tuple[UUID, ...]
    preview_artifact_id: UUID | None
    created_at: datetime


class ProjectContextModel(ContractModel):
    project: ProjectModel
    initial_version: VersionModel


class TaskContextModel(ContractModel):
    task: TaskModel
    target_version: VersionModel | None
    scope: ScopeContractModel


class PendingChangesetModel(ContractModel):
    changeset: ChangesetModel
    approval: ApprovalModel


class HealthModel(ContractModel):
    status: str
    service: str
    protocol: str


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
    command_metadata: list[dict[str, Any]] = Field(default_factory=list)
    schema_version: int = 1
