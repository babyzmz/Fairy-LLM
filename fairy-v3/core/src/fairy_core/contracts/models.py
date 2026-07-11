from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, RootModel, field_validator, model_validator

from fairy_core.assistant.models import (
    AssistantTurnStatus,
    MessageRole,
)
from fairy_core.commanding.types import PermissionProfile
from fairy_core.contracts.common import (
    ContractModel,
    ErrorCode,
    EventVisibilityModel,
    ExecutionTarget,
    JsonValue,
    PermissionProfileModel,
    PublicMessageVisibilityModel,
)
from fairy_core.documents import (
    DocumentStatus,
    DocumentVisibility,
)
from fairy_core.domain.execution import (
    ApprovalDecision,
    ArtifactType,
    ArtifactVisibility,
    ChangesetStatus,
    PreviewHealth,
    PreviewStatus,
    PreviewVisibility,
    RuntimeHealth,
    RuntimeKind,
    RuntimeStatus,
)
from fairy_core.domain.models import (
    OperationMode,
    ProjectResidency,
    TaskStatus,
    VersionVisibility,
    WorkspaceType,
)
from fairy_core.memory.models import (
    ClaimStatus,
    MemoryAuthority,
    MemoryNamespace,
    MemoryScanResult,
    MemorySensitivity,
    MemorySourceType,
    MemoryTargetKind,
    ObservationStatus,
)
from fairy_core.memory.retrieval_models import (
    MemorySelectionReason,
    MemorySnapshotStatus,
    MemorySourceKind,
    ProjectionState,
)
from fairy_core.perception import ImagePersistence
from fairy_core.providers.models import (
    ProviderCapability,
    ProviderHealthStatus,
    ProviderKind,
)
from fairy_core.voice import AudioMediaType


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


class CollectionPageInput(ContractModel):
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=2_048)

    @field_validator("cursor")
    @classmethod
    def require_nonblank_cursor(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("cursor must not be blank")
        return value


class ProjectListInput(CollectionPageInput):
    pass


class ConversationListInput(CollectionPageInput):
    project_id: UUID | None = None


class TaskListInput(CollectionPageInput):
    project_id: UUID | None = None
    conversation_id: UUID | None = None


class VersionListInput(CollectionPageInput):
    project_id: UUID | None = None
    conversation_id: UUID | None = None
    task_id: UUID | None = None


class ApprovalListInput(VersionListInput):
    pass


class ApprovalDecisionInput(ContractModel):
    approval_id: UUID
    approved: bool
    decided_by: str = Field(min_length=1, max_length=255)


class TaskIdInput(ContractModel):
    task_id: UUID


class ProjectIdInput(ContractModel):
    project_id: UUID


class ConversationIdInput(ContractModel):
    conversation_id: UUID


class VersionIdInput(ContractModel):
    version_id: UUID


class RuntimeIdInput(ContractModel):
    runtime_id: UUID


class PreviewIdInput(ContractModel):
    preview_id: UUID


class ArtifactIdInput(ContractModel):
    artifact_id: UUID


class DocumentIdInput(TaskIdInput):
    document_id: UUID


class DocumentImportInput(TaskIdInput):
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=3, max_length=255)
    content_base64: str = Field(min_length=1, max_length=28_000_000)
    visibility: DocumentVisibility
    idempotency_key: str = Field(min_length=1, max_length=512)
    user_confirmed: bool


class DocumentDeleteInput(DocumentIdInput):
    idempotency_key: str = Field(min_length=1, max_length=512)
    user_confirmed: bool


class DocumentListInput(TaskIdInput):
    limit: int = Field(default=100, ge=1, le=100)


class DocumentSearchInput(TaskIdInput):
    query: str = Field(min_length=1, max_length=10_000)
    limit: int = Field(default=10, ge=1, le=50)


class AssistantTurnIdInput(ContractModel):
    turn_id: UUID


class ImageAttachmentInput(ContractModel):
    media_type: Literal["image/png"]
    png_base64: str = Field(min_length=1, max_length=28_000_000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(ge=1, le=16_384)
    height: int = Field(ge=1, le=16_384)
    source_label: str = Field(min_length=1, max_length=255)
    captured_at_ms: int = Field(ge=0, le=100_000_000_000_000)
    persistence: ImagePersistence


class AssistantTurnCreateInput(ContractModel):
    task_id: UUID
    profile_id: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=512)
    image_attachments: tuple[ImageAttachmentInput, ...] = Field(
        default_factory=tuple,
        max_length=4,
    )


class AssistantTurnCancelInput(AssistantTurnIdInput):
    expected_cancellation_revision: int = Field(ge=0)


class AssistantTurnRunInput(AssistantTurnIdInput):
    pass


class AssistantTurnRetryInput(AssistantTurnIdInput):
    idempotency_key: str = Field(min_length=1, max_length=512)


class MessageListInput(CollectionPageInput):
    conversation_id: UUID


class RuntimeHealthInput(TaskIdInput):
    pass


class PreviewStartInput(TaskIdInput):
    idempotency_key: str = Field(min_length=1, max_length=255)


class PreviewStopInput(PreviewIdInput):
    idempotency_key: str = Field(min_length=1, max_length=255)


class PreviewResolveInput(ContractModel):
    conversation_id: UUID
    preview_id: UUID | None = None


class ArtifactListInput(TaskIdInput):
    pass


class VersionAcceptInput(TaskIdInput):
    expected_project_revision: int = Field(ge=0)
    user_confirmed: bool


class CapabilityRequest(ContractModel):
    pass


class ExecutionSettingsUpdateInput(ContractModel):
    profile: PermissionProfile
    capability_overrides: dict[str, bool] = Field(default_factory=dict, max_length=256)
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=512)


class ExecutionSettingsModel(ContractModel):
    profile: PermissionProfile
    capability_overrides: dict[str, bool]
    revision: int = Field(ge=0)
    updated_at: datetime


class ProviderHealthInput(ContractModel):
    profile_id: str | None = Field(default=None, min_length=1, max_length=128)


class VoiceTranscribeInput(ContractModel):
    conversation_id: UUID
    profile_id: str = Field(min_length=1, max_length=128)
    media_type: AudioMediaType
    audio_base64: str = Field(min_length=1, max_length=28_000_000)
    language: str | None = Field(default=None, min_length=2, max_length=71)


class VoiceSynthesizeInput(TaskIdInput):
    turn_id: UUID
    message_id: UUID
    profile_id: str = Field(min_length=1, max_length=128)
    voice: str = Field(min_length=1, max_length=64)
    start_offset: int = Field(ge=0, le=100_000)
    end_offset: int = Field(gt=0, le=100_000)

    @model_validator(mode="after")
    def require_non_empty_range(self) -> VoiceSynthesizeInput:
        if self.end_offset <= self.start_offset:
            raise ValueError("voice synthesis range must be non-empty")
        return self


class ProviderProfileModel(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=255)
    kind: ProviderKind
    base_url: str = Field(min_length=1, max_length=2_048)
    model_id: str = Field(min_length=1, max_length=255)
    capabilities: tuple[ProviderCapability, ...]
    fallback_profile_id: str | None = Field(default=None, max_length=128)
    timeout_seconds: float = Field(gt=0, le=300)
    enabled: bool
    credential_required: bool
    credential_configured: bool


class ProviderProfilePageModel(ContractModel):
    items: tuple[ProviderProfileModel, ...]


class ProviderHealthModel(ContractModel):
    profile_id: str = Field(min_length=1, max_length=128)
    status: ProviderHealthStatus
    error_code: str | None = Field(default=None, max_length=128)
    diagnostics: tuple[str, ...]


class ProviderHealthPageModel(ContractModel):
    items: tuple[ProviderHealthModel, ...]


class TranscriptSegmentModel(ContractModel):
    index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=20_000)
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(ge=0, allow_inf_nan=False)


class VoiceTranscriptModel(ContractModel):
    conversation_id: UUID
    profile_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=100_000)
    language: str | None = Field(default=None, min_length=2, max_length=71)
    segments: tuple[TranscriptSegmentModel, ...]


class VoiceAudioModel(ContractModel):
    task_id: UUID
    turn_id: UUID
    message_id: UUID
    profile_id: str = Field(min_length=1, max_length=128)
    start_offset: int = Field(ge=0, le=100_000)
    end_offset: int = Field(gt=0, le=100_000)
    media_type: Literal["audio/wav"]
    audio_base64: str = Field(min_length=1, max_length=28_000_000)
    sample_rate: int = Field(ge=8_000, le=48_000)
    channels: int = Field(ge=1, le=2)
    frames: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


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
    memory_snapshot_id: UUID | None
    memory_snapshot_hash: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    status: TaskStatus
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def require_memory_snapshot_pair(self) -> TaskModel:
        _validate_memory_snapshot_pair(
            self.memory_snapshot_id,
            self.memory_snapshot_hash,
        )
        return self


class VersionModel(ContractModel):
    id: UUID
    project_id: UUID
    source_conversation_id: UUID | None
    source_task_id: UUID | None
    parent_version_id: UUID | None
    project_root: Path
    visibility: VersionVisibility
    created_at: datetime


class RuntimeModel(ContractModel):
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    project_root: Path
    execution_target: ExecutionTarget
    kind: RuntimeKind
    executor: str = Field(min_length=1, max_length=128)
    executor_handle: str | None
    port: int | None = Field(default=None, ge=1, le=65_535)
    status: RuntimeStatus
    health: RuntimeHealth
    error_code: str | None
    idempotency_key: str = Field(min_length=1, max_length=512)
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def require_consistent_runtime_state(self) -> RuntimeModel:
        active = self.status in {RuntimeStatus.RUNNING, RuntimeStatus.STOPPING}
        paired_endpoint = self.executor_handle is not None and self.port is not None
        mismatched_endpoint = (self.executor_handle is None) != (self.port is None)
        inactive = self.status in {
            RuntimeStatus.CREATED,
            RuntimeStatus.STARTING,
            RuntimeStatus.STOPPED,
        }
        if (
            mismatched_endpoint
            or (active and not paired_endpoint)
            or (inactive and paired_endpoint)
        ):
            raise ValueError("active Runtime requires executor handle and port")
        if self.kind is RuntimeKind.STATIC_SITE and (
            self.execution_target is not ExecutionTarget.LOCAL
            or self.project_id is None
            or self.version_id is None
        ):
            raise ValueError("static Runtime requires a local Project Version")
        return self


class PreviewModel(ContractModel):
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    runtime_id: UUID
    project_root: Path
    execution_target: ExecutionTarget
    url: str | None
    visibility: PreviewVisibility
    status: PreviewStatus
    health: PreviewHealth
    error_code: str | None
    idempotency_key: str = Field(min_length=1, max_length=512)
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def require_scoped_preview_url(self) -> PreviewModel:
        requires_url = self.status in {PreviewStatus.READY, PreviewStatus.STOPPING}
        forbids_url = self.status in {
            PreviewStatus.CREATED,
            PreviewStatus.STARTING,
            PreviewStatus.STOPPED,
            PreviewStatus.FAILED,
        }
        if requires_url and self.url is None:
            raise ValueError("active Preview requires a URL")
        if forbids_url and self.url is not None:
            raise ValueError("inactive Preview cannot retain a URL")
        if self.url is None:
            return self
        try:
            parsed = urlsplit(self.url)
            port = parsed.port
        except ValueError as error:
            raise ValueError("Preview URL is invalid") from error
        common_invalid = (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query != ""
            or parsed.fragment != ""
            or not parsed.path.startswith("/")
        )
        if self.execution_target is ExecutionTarget.LOCAL:
            invalid = (
                parsed.scheme != "http"
                or parsed.hostname != "127.0.0.1"
                or port is None
                or common_invalid
            )
        else:
            invalid = parsed.scheme != "https" or parsed.hostname is None or common_invalid
        if invalid:
            raise ValueError("Preview URL does not match its execution target")
        return self


class ArtifactModel(ContractModel):
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    artifact_type: ArtifactType
    visibility: ArtifactVisibility
    storage_location: str = Field(min_length=1, max_length=4_096)
    media_type: str = Field(min_length=3, max_length=255)
    byte_length: int = Field(ge=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    metadata: dict[str, Any]
    created_at: datetime

    @field_validator("metadata", mode="before")
    @classmethod
    def thaw_metadata(cls, value: Any) -> Any:
        return _mutable_json(value)


class ManagedDocumentModel(ContractModel):
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    source_task_id: UUID
    version_id: UUID | None
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=3, max_length=255)
    byte_length: int = Field(ge=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    current_revision: int = Field(ge=1)
    visibility: DocumentVisibility
    status: DocumentStatus
    idempotency_key: str = Field(min_length=1, max_length=512)
    created_at: datetime
    updated_at: datetime


class DocumentRevisionModel(ContractModel):
    document_id: UUID
    revision: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(ge=0)
    media_type: str = Field(min_length=3, max_length=255)
    parser: str = Field(min_length=1, max_length=128)
    parser_version: str = Field(min_length=1, max_length=128)
    section_count: int = Field(ge=1, le=10_000)
    chunk_count: int = Field(ge=1, le=100_000)
    created_at: datetime


class DocumentChunkModel(ContractModel):
    id: UUID
    document_id: UUID
    revision: int = Field(ge=1)
    revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    ordinal: int = Field(ge=0)
    section_ordinal: int = Field(ge=0)
    locator: dict[str, str | int]
    text: str = Field(min_length=1, max_length=20_000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    token_count: int = Field(ge=1, le=20_000)
    updated_at: datetime

    @field_validator("locator", mode="before")
    @classmethod
    def thaw_locator(cls, value: Any) -> Any:
        return _mutable_json(value)


class DocumentContextModel(ContractModel):
    document: ManagedDocumentModel
    revision: DocumentRevisionModel


class DocumentPageModel(ContractModel):
    items: tuple[DocumentContextModel, ...]


class DocumentSearchHitModel(ContractModel):
    document: ManagedDocumentModel
    revision: DocumentRevisionModel
    chunk: DocumentChunkModel
    lexical_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    exact_match: bool


class DocumentSearchPageModel(ContractModel):
    items: tuple[DocumentSearchHitModel, ...]


class MessageModel(ContractModel):
    id: UUID
    conversation_id: UUID
    task_id: UUID
    turn_id: UUID | None
    sequence: int = Field(ge=1)
    role: MessageRole
    visibility: PublicMessageVisibilityModel
    content: str = Field(min_length=1, max_length=1_000_000)
    created_at: datetime


class MessagePageModel(ContractModel):
    items: tuple[MessageModel, ...]
    next_cursor: str | None


class AssistantTurnModel(ContractModel):
    id: UUID
    conversation_id: UUID
    task_id: UUID
    profile_id: str = Field(min_length=1, max_length=255)
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    memory_snapshot_id: UUID
    memory_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=512)
    status: AssistantTurnStatus
    cancellation_revision: int = Field(ge=0)
    usage: dict[str, int]
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    @field_validator("usage")
    @classmethod
    def require_non_negative_usage(cls, value: dict[str, int]) -> dict[str, int]:
        if any(isinstance(count, bool) or count < 0 for count in value.values()):
            raise ValueError("usage values must be non-negative integers")
        return value


class RuntimeExecutorHealthModel(ContractModel):
    available: bool
    executor: str = Field(min_length=1, max_length=128)
    version: str | None
    error_code: str | None
    diagnostics: tuple[str, ...]

    @model_validator(mode="after")
    def require_consistent_availability(self) -> RuntimeExecutorHealthModel:
        if self.available == (self.error_code is not None):
            raise ValueError("Runtime executor availability and error code are inconsistent")
        return self


class RuntimeHealthModel(ContractModel):
    executor: RuntimeExecutorHealthModel
    runtime: RuntimeModel | None
    preview: PreviewModel | None


class PreviewContextModel(ContractModel):
    task: TaskModel
    runtime: RuntimeModel
    preview: PreviewModel


class PreviewResolutionModel(RootModel[PreviewContextModel | None]):
    pass


class ArtifactPageModel(ContractModel):
    items: tuple[ArtifactModel, ...]


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
    memory_snapshot_id: UUID | None
    memory_snapshot_hash: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    scope_digest: str

    @model_validator(mode="after")
    def require_memory_snapshot_pair(self) -> ScopeContractModel:
        _validate_memory_snapshot_pair(
            self.memory_snapshot_id,
            self.memory_snapshot_hash,
        )
        return self


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


class ProjectPageModel(ContractModel):
    items: tuple[ProjectModel, ...]
    next_cursor: str | None


class ConversationPageModel(ContractModel):
    items: tuple[ConversationModel, ...]
    next_cursor: str | None


class TaskPageModel(ContractModel):
    items: tuple[TaskModel, ...]
    next_cursor: str | None


class VersionPageModel(ContractModel):
    items: tuple[VersionModel, ...]
    next_cursor: str | None


class ApprovalPageModel(ContractModel):
    items: tuple[ApprovalModel, ...]
    next_cursor: str | None


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


class MemoryForgetTargetModel(StrEnum):
    OBSERVATION = "observation"
    CLAIM = "claim"


class MemoryObserveInput(ContractModel):
    task_id: UUID
    content: str = Field(min_length=1, max_length=100_000)
    idempotency_key: str = Field(min_length=1, max_length=255)


class MemoryObservationQuery(ContractModel):
    task_id: UUID
    namespace: MemoryNamespace


class MemoryClaimGetInput(ContractModel):
    task_id: UUID
    claim_id: UUID


class MemoryClaimQuery(ContractModel):
    task_id: UUID
    namespace: MemoryNamespace


class MemorySearchInput(ContractModel):
    task_id: UUID
    query: str = Field(min_length=1, max_length=10_000)
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def require_nonblank_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("memory search query is required")
        return normalized


class MemorySnapshotGetInput(ContractModel):
    task_id: UUID
    snapshot_id: UUID


class MemoryProjectionHealthInput(ContractModel):
    task_id: UUID


class _MemoryClaimValueInput(ContractModel):
    task_id: UUID
    value: JsonValue
    normalized_text: str = Field(min_length=1, max_length=100_000)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    user_confirmed: bool
    idempotency_key: str = Field(min_length=1, max_length=255)

    @field_validator("value")
    @classmethod
    def require_strict_json_value(cls, value: JsonValue) -> JsonValue:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("memory Claim value must be valid JSON") from error
        return value


class MemoryClaimPromoteInput(_MemoryClaimValueInput):
    observation_id: UUID
    subject: str = Field(min_length=1, max_length=512)
    predicate: str = Field(min_length=1, max_length=512)


class MemoryClaimSupersedeInput(_MemoryClaimValueInput):
    claim_id: UUID
    expected_revision: int = Field(ge=1)
    source_observation_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)


class MemoryClaimResolveInput(MemoryClaimSupersedeInput):
    resolved_claim_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)


class MemoryForgetInput(ContractModel):
    task_id: UUID
    target_kind: MemoryForgetTargetModel
    target_id: UUID
    reason: str = Field(min_length=1, max_length=10_000)
    user_confirmed: bool
    idempotency_key: str = Field(min_length=1, max_length=255)


class MemoryObservationModel(ContractModel):
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    scope_digest: str
    source_event_id: UUID
    source_cursor: int = Field(ge=1)
    source_type: MemorySourceType
    content: str
    content_hash: str
    proposed_namespace: MemoryNamespace
    authority: MemoryAuthority
    confidence: float = Field(ge=0, le=1)
    sensitivity: MemorySensitivity
    scan_result: MemoryScanResult
    status: ObservationStatus
    actor: str
    created_at: datetime


class MemoryObservationPageModel(ContractModel):
    items: tuple[MemoryObservationModel, ...]


class MemoryClaimModel(ContractModel):
    id: UUID
    namespace: MemoryNamespace
    project_id: UUID | None
    conversation_id: UUID | None
    task_id: UUID | None
    version_id: UUID | None
    device_id: str | None
    subject: str
    predicate: str
    current_revision: int = Field(ge=0)
    conflict_set_id: UUID | None
    status: ClaimStatus
    created_at: datetime
    updated_at: datetime


class MemoryClaimRevisionModel(ContractModel):
    claim_id: UUID
    revision: int = Field(ge=1)
    value: JsonValue
    normalized_text: str
    source_observation_ids: tuple[UUID, ...]
    source_event_ids: tuple[UUID, ...]
    authority: MemoryAuthority
    confidence: float = Field(ge=0, le=1)
    valid_from: datetime | None
    valid_to: datetime | None
    recorded_at: datetime
    actor: str
    supersedes_revision: int | None
    resolved_claim_ids: tuple[UUID, ...]


class MemoryClaimContextModel(ContractModel):
    claim: MemoryClaimModel
    current_revision: MemoryClaimRevisionModel


class MemoryClaimPageModel(ContractModel):
    items: tuple[MemoryClaimContextModel, ...]


class MemoryTombstoneModel(ContractModel):
    id: UUID
    target_kind: MemoryTargetKind
    target_id: UUID
    reason: str
    actor: str
    source_event_id: UUID
    created_at: datetime


class MemorySearchDocumentModel(ContractModel):
    id: UUID
    source_kind: MemorySourceKind
    source_id: UUID
    source_revision: int | None = Field(default=None, ge=1)
    namespace: MemoryNamespace | None
    project_id: UUID | None
    conversation_id: UUID | None
    task_id: UUID | None
    version_id: UUID | None
    language: str = Field(min_length=1, max_length=32)
    normalized_text: str = Field(min_length=1, max_length=100_000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_cursor: int = Field(ge=1)
    projection_generation: int = Field(ge=1)
    updated_at: datetime

    @model_validator(mode="after")
    def require_consistent_source(self) -> MemorySearchDocumentModel:
        expected_hash = hashlib.sha256(self.normalized_text.encode("utf-8")).hexdigest()
        if self.content_hash != expected_hash:
            raise ValueError("content_hash does not match normalized_text")
        if self.source_kind is MemorySourceKind.CLAIM_REVISION and self.source_revision is None:
            raise ValueError("Claim revision document requires source_revision")
        if self.namespace is MemoryNamespace.PROJECT_CANONICAL and self.project_id is None:
            raise ValueError("project_canonical document requires project_id")
        if self.namespace is MemoryNamespace.CONVERSATION_DRAFT and self.conversation_id is None:
            raise ValueError("conversation_draft document requires conversation_id")
        return self


class MemorySearchHitModel(ContractModel):
    document: MemorySearchDocumentModel
    lexical_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    exact_match: bool


class MemorySearchPageModel(ContractModel):
    items: tuple[MemorySearchHitModel, ...]


class MemorySnapshotItemModel(ContractModel):
    ordinal: int = Field(ge=0)
    source_kind: MemorySourceKind
    source_id: UUID
    source_revision: int | None = Field(default=None, ge=1)
    namespace: MemoryNamespace | None
    selection_reason: MemorySelectionReason
    authority: MemoryAuthority
    score_components: dict[str, float]
    rendered_text: str = Field(min_length=1, max_length=100_000)
    rendered_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    token_count: int = Field(ge=1, le=3_000)

    @field_validator("score_components")
    @classmethod
    def require_finite_score_components(
        cls,
        value: dict[str, float],
    ) -> dict[str, float]:
        for name, score in value.items():
            if not name.strip() or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("score components must be named, finite, and between 0 and 1")
        return value

    @model_validator(mode="after")
    def require_rendered_text_hash(self) -> MemorySnapshotItemModel:
        expected_hash = hashlib.sha256(self.rendered_text.encode("utf-8")).hexdigest()
        if self.rendered_text_hash != expected_hash:
            raise ValueError("rendered_text_hash does not match rendered_text")
        return self


class MemorySnapshotModel(ContractModel):
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    base_version_id: UUID | None
    target_version_id: UUID | None
    snapshot_version: int = Field(ge=1)
    policy_version: str = Field(min_length=1, max_length=128)
    source_watermark_cursor: int = Field(ge=0)
    projection_generation: int = Field(ge=1)
    projection_watermark_cursor: int = Field(ge=0)
    projection_state: ProjectionState
    status: MemorySnapshotStatus
    degraded_reason: str | None = Field(default=None, min_length=1, max_length=128)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    token_count: int = Field(ge=0, le=3_000)
    items: tuple[MemorySnapshotItemModel, ...]
    created_at: datetime

    @model_validator(mode="after")
    def require_consistent_manifest(self) -> MemorySnapshotModel:
        if tuple(item.ordinal for item in self.items) != tuple(range(len(self.items))):
            raise ValueError("Snapshot item ordinals must be contiguous from zero")
        if self.token_count != sum(item.token_count for item in self.items):
            raise ValueError("Snapshot token_count must equal item token counts")
        if self.status is MemorySnapshotStatus.READY:
            if self.projection_state is not ProjectionState.READY:
                raise ValueError("READY Snapshot requires READY projection state")
            if self.projection_watermark_cursor < self.source_watermark_cursor:
                raise ValueError("READY Snapshot projection cannot trail source")
            if self.degraded_reason is not None:
                raise ValueError("READY Snapshot cannot have a degraded reason")
        else:
            if self.projection_state is ProjectionState.READY:
                raise ValueError("DEGRADED Snapshot cannot report READY projection state")
            if self.degraded_reason is None:
                raise ValueError("DEGRADED Snapshot requires a degraded reason")
        return self


class MemoryProjectionHealthModel(ContractModel):
    generation: int = Field(ge=1)
    state: ProjectionState
    source_watermark_cursor: int = Field(ge=0)
    projected_watermark_cursor: int = Field(ge=0)
    lag: int = Field(ge=0)
    last_error_code: str | None = Field(default=None, min_length=1, max_length=128)
    updated_at: datetime

    @model_validator(mode="after")
    def require_consistent_ready_state(self) -> MemoryProjectionHealthModel:
        expected_lag = max(
            0,
            self.source_watermark_cursor - self.projected_watermark_cursor,
        )
        if self.lag != expected_lag:
            raise ValueError("projection lag does not match its watermarks")
        if self.state is ProjectionState.READY:
            if self.projected_watermark_cursor < self.source_watermark_cursor:
                raise ValueError("READY projection cannot trail source")
            if self.last_error_code is not None:
                raise ValueError("READY projection cannot carry an error code")
        return self


def _mutable_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _mutable_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mutable_json(item) for item in value]
    return value


def _validate_memory_snapshot_pair(
    snapshot_id: UUID | None,
    snapshot_hash: str | None,
) -> None:
    if (snapshot_id is None) != (snapshot_hash is None):
        raise ValueError("memory Snapshot ID and hash must be both present")
