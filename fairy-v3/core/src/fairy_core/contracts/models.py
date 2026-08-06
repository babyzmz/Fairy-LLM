from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
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
    PublicMessageVisibilityModel,
)
from fairy_core.contracts.common import (
    PermissionProfileModel as PermissionProfileModel,
)
from fairy_core.contracts.history_models import ConversationModel, ProjectModel
from fairy_core.contracts.memory_models import (
    MemoryClaimContextModel as MemoryClaimContextModel,
)
from fairy_core.contracts.memory_models import (
    MemoryClaimGetInput as MemoryClaimGetInput,
)
from fairy_core.contracts.memory_models import (
    MemoryClaimModel as MemoryClaimModel,
)
from fairy_core.contracts.memory_models import (
    MemoryClaimPageModel as MemoryClaimPageModel,
)
from fairy_core.contracts.memory_models import (
    MemoryClaimPromoteInput as MemoryClaimPromoteInput,
)
from fairy_core.contracts.memory_models import (
    MemoryClaimQuery as MemoryClaimQuery,
)
from fairy_core.contracts.memory_models import (
    MemoryClaimResolveInput as MemoryClaimResolveInput,
)
from fairy_core.contracts.memory_models import (
    MemoryClaimRevisionModel as MemoryClaimRevisionModel,
)
from fairy_core.contracts.memory_models import (
    MemoryClaimSupersedeInput as MemoryClaimSupersedeInput,
)
from fairy_core.contracts.memory_models import (
    MemoryForgetInput as MemoryForgetInput,
)
from fairy_core.contracts.memory_models import (
    MemoryForgetTargetModel as MemoryForgetTargetModel,
)
from fairy_core.contracts.memory_models import (
    MemoryObservationModel as MemoryObservationModel,
)
from fairy_core.contracts.memory_models import (
    MemoryObservationPageModel as MemoryObservationPageModel,
)
from fairy_core.contracts.memory_models import (
    MemoryObservationQuery as MemoryObservationQuery,
)
from fairy_core.contracts.memory_models import (
    MemoryObserveInput as MemoryObserveInput,
)
from fairy_core.contracts.memory_models import (
    MemoryProjectionHealthInput as MemoryProjectionHealthInput,
)
from fairy_core.contracts.memory_models import (
    MemoryProposalActionInput as MemoryProposalActionInput,
)
from fairy_core.contracts.memory_models import (
    MemoryProposalListInput as MemoryProposalListInput,
)
from fairy_core.contracts.memory_models import (
    MemoryProposalModel as MemoryProposalModel,
)
from fairy_core.contracts.memory_models import (
    MemoryProposalPageModel as MemoryProposalPageModel,
)
from fairy_core.contracts.memory_models import (
    MemorySearchInput as MemorySearchInput,
)
from fairy_core.contracts.memory_models import (
    MemorySettingsModel as MemorySettingsModel,
)
from fairy_core.contracts.memory_models import (
    MemorySettingsUpdateInput as MemorySettingsUpdateInput,
)
from fairy_core.contracts.memory_models import (
    MemorySnapshotGetInput as MemorySnapshotGetInput,
)
from fairy_core.contracts.memory_models import (
    MemorySuggestInput as MemorySuggestInput,
)
from fairy_core.contracts.memory_models import (
    MemoryTombstoneModel as MemoryTombstoneModel,
)
from fairy_core.contracts.memory_snapshot_models import (
    MemoryProjectionHealthModel as MemoryProjectionHealthModel,
)
from fairy_core.contracts.memory_snapshot_models import (
    MemorySearchDocumentModel as MemorySearchDocumentModel,
)
from fairy_core.contracts.memory_snapshot_models import (
    MemorySearchHitModel as MemorySearchHitModel,
)
from fairy_core.contracts.memory_snapshot_models import (
    MemorySearchPageModel as MemorySearchPageModel,
)
from fairy_core.contracts.memory_snapshot_models import (
    MemorySnapshotItemModel as MemorySnapshotItemModel,
)
from fairy_core.contracts.memory_snapshot_models import (
    MemorySnapshotModel as MemorySnapshotModel,
)
from fairy_core.contracts.model_routing import (
    ModelSelectionSnapshotInput,
    ModelSelectionSnapshotModel,
    RoutingDecisionModel,
)
from fairy_core.contracts.provider_models import (
    ProviderHealthInput as ProviderHealthInput,
)
from fairy_core.contracts.provider_models import (
    ProviderHealthModel as ProviderHealthModel,
)
from fairy_core.contracts.provider_models import (
    ProviderHealthPageModel as ProviderHealthPageModel,
)
from fairy_core.contracts.provider_models import (
    ProviderProfileModel as ProviderProfileModel,
)
from fairy_core.contracts.provider_models import (
    ProviderProfilePageModel as ProviderProfilePageModel,
)
from fairy_core.contracts.runtime import PreviewModel, RuntimeModel
from fairy_core.documents import (
    DocumentStatus,
    DocumentVisibility,
)
from fairy_core.domain.execution import (
    ApprovalDecision,
    ArtifactType,
    ArtifactVisibility,
    ChangesetStatus,
)
from fairy_core.domain.models import (
    OperationMode,
    ProjectResidency,
    TaskStatus,
    VersionVisibility,
    WorkspaceType,
)
from fairy_core.perception import ImagePersistence
from fairy_core.voice import AudioMediaType
from fairy_core.workflow.models import WorkflowBudgetTier, WorkflowRunStatus


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
    workspace_id: UUID | None = None
    project_id: UUID | None = None
    conversation_id: UUID | None = None
    task_id: UUID | None = None


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
    profile_id: str | None = Field(default=None, min_length=1, max_length=255)
    model_selection: ModelSelectionSnapshotInput | None = None
    idempotency_key: str = Field(min_length=1, max_length=512)
    image_attachments: tuple[ImageAttachmentInput, ...] = Field(
        default_factory=tuple,
        max_length=4,
    )

    @model_validator(mode="after")
    def require_one_model_source(self) -> AssistantTurnCreateInput:
        if (self.profile_id is None) == (self.model_selection is None):
            raise ValueError("provide exactly one of profile_id or model_selection")
        return self


class AssistantTurnCancelInput(AssistantTurnIdInput):
    expected_cancellation_revision: int = Field(ge=0)


class AssistantTurnRunInput(AssistantTurnIdInput):
    pass


class AssistantTurnStartInput(AssistantTurnIdInput):
    pass


class AssistantTurnRetryInput(AssistantTurnIdInput):
    idempotency_key: str = Field(min_length=1, max_length=512)


class MessageListInput(CollectionPageInput):
    conversation_id: UUID


class RuntimeHealthInput(TaskIdInput):
    pass


class PreviewActivateInput(TaskIdInput):
    workspace_id: UUID
    version_id: UUID
    expected_workspace_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=255)


class PreviewStartInput(TaskIdInput):
    workspace_id: UUID
    version_id: UUID
    expected_workspace_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=255)


class PreviewStopInput(PreviewIdInput):
    task_id: UUID
    workspace_id: UUID
    version_id: UUID
    expected_workspace_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=255)


class PreviewResolveInput(ContractModel):
    task_id: UUID
    workspace_id: UUID
    version_id: UUID
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


class TaskModel(ContractModel):
    id: UUID
    project_id: UUID | None
    workspace_id: UUID
    conversation_id: UUID
    user_request: str
    operation_mode: OperationMode
    base_version_id: UUID | None
    execution_target: ExecutionTarget
    target_version_id: UUID | None
    memory_snapshot_id: UUID | None
    memory_snapshot_hash: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    knowledge_snapshot_id: UUID | None = None
    knowledge_snapshot_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    harness_manifest_id: UUID | None = None
    harness_manifest_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    status: TaskStatus
    display_title: str
    pinned_at: datetime | None
    metadata_revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def require_memory_snapshot_pair(self) -> TaskModel:
        _validate_memory_snapshot_pair(
            self.memory_snapshot_id,
            self.memory_snapshot_hash,
        )
        _validate_binding_pair(
            self.knowledge_snapshot_id,
            self.knowledge_snapshot_hash,
            "Knowledge Snapshot",
        )
        _validate_binding_pair(
            self.harness_manifest_id,
            self.harness_manifest_hash,
            "Harness Manifest",
        )
        return self


class VersionModel(ContractModel):
    id: UUID
    project_id: UUID | None
    workspace_id: UUID
    source_conversation_id: UUID | None
    source_task_id: UUID | None
    parent_version_id: UUID | None
    project_root: Path
    visibility: VersionVisibility
    created_at: datetime


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


class AssistantWorkflowSummaryModel(ContractModel):
    run_id: UUID
    status: WorkflowRunStatus
    budget_tier: WorkflowBudgetTier
    active_plan_revision: int = Field(ge=1)
    current_phase: str | None = Field(default=None, max_length=128)
    public_summary: str | None = Field(default=None, max_length=500)
    completed_nodes: int = Field(ge=0)
    total_nodes: int = Field(ge=1)
    model_rounds_used: int = Field(ge=0)
    max_model_rounds: int = Field(ge=1)
    tool_invocations_used: int = Field(ge=0)
    max_tool_invocations: int = Field(ge=1)
    pause_requested: bool
    updated_at: datetime


class AssistantTurnModel(ContractModel):
    id: UUID
    conversation_id: UUID
    task_id: UUID
    profile_id: str = Field(min_length=1, max_length=255)
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    memory_snapshot_id: UUID
    memory_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    knowledge_snapshot_id: UUID | None = None
    knowledge_snapshot_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    harness_manifest_id: UUID | None = None
    harness_manifest_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    idempotency_key: str = Field(min_length=1, max_length=512)
    model_selection: ModelSelectionSnapshotModel | None
    routing_decision: RoutingDecisionModel | None
    budget_approval_run_id: UUID | None
    cited_evidence_receipt_ids: tuple[UUID, ...] = Field(default=(), max_length=32)
    workflow_run_id: UUID | None = None
    execution_engine_version: int = Field(default=1, ge=1)
    workflow_summary: AssistantWorkflowSummaryModel | None = None
    status: AssistantTurnStatus
    cancellation_revision: int = Field(ge=0)
    usage: dict[str, int]
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    @model_validator(mode="after")
    def require_harness_binding_pairs(self) -> AssistantTurnModel:
        _validate_binding_pair(
            self.knowledge_snapshot_id,
            self.knowledge_snapshot_hash,
            "Knowledge Snapshot",
        )
        _validate_binding_pair(
            self.harness_manifest_id,
            self.harness_manifest_hash,
            "Harness Manifest",
        )
        if self.workflow_run_id is None:
            if self.workflow_summary is not None:
                raise ValueError("Workflow summary requires a Workflow Run binding")
        elif self.execution_engine_version < 2:
            raise ValueError("Workflow-backed Assistant Turns require execution engine v2")
        elif (
            self.workflow_summary is not None
            and self.workflow_summary.run_id != self.workflow_run_id
        ):
            raise ValueError("Workflow summary does not match the Assistant Turn binding")
        return self

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


class PreviewActivationModel(ContractModel):
    outcome: Literal["ready", "starting", "waiting_for_slot", "not_runnable", "failed"]
    context: PreviewContextModel | None
    adapter: str | None = Field(default=None, min_length=1, max_length=32)
    capacity: int = Field(ge=1, le=16)
    active_count: int = Field(ge=0, le=16)
    evicted_preview_id: UUID | None = None
    public_reason: str | None = Field(default=None, min_length=1, max_length=500)


class PreviewResolutionModel(RootModel[PreviewContextModel | None]):
    pass


class ArtifactPageModel(ContractModel):
    items: tuple[ArtifactModel, ...]


class ScopeContractModel(ContractModel):
    workspace_type: WorkspaceType
    project_id: UUID | None
    workspace_id: UUID
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
    knowledge_snapshot_id: UUID | None = None
    knowledge_snapshot_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    scope_digest: str

    @model_validator(mode="after")
    def require_memory_snapshot_pair(self) -> ScopeContractModel:
        _validate_memory_snapshot_pair(
            self.memory_snapshot_id,
            self.memory_snapshot_hash,
        )
        _validate_binding_pair(
            self.knowledge_snapshot_id,
            self.knowledge_snapshot_hash,
            "Knowledge Snapshot",
        )
        return self


class ChangesetModel(ContractModel):
    id: UUID
    project_id: UUID | None
    workspace_id: UUID
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
    tool_invocation_id: UUID | None
    decision: ApprovalDecision
    decided_by: str | None
    created_at: datetime
    decided_at: datetime | None


class ApprovalDecisionResultModel(ContractModel):
    approval: ApprovalModel
    changeset: ChangesetModel | None
    assistant_turn_id: UUID | None
    resume_requested: bool


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
    evidence_artifact_ids: tuple[UUID, ...]
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


class FileMutationOperation(StrEnum):
    UPSERT = "upsert"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    RENAME = "rename"


class FileMutation(ContractModel):
    operation: FileMutationOperation = FileMutationOperation.UPSERT
    path: str = Field(min_length=1, max_length=1_024)
    destination_path: str | None = Field(default=None, min_length=1, max_length=1_024)
    content: str | None = Field(default=None, max_length=2_097_152)
    content_base64: str | None = Field(default=None, max_length=2_796_204)
    expected_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

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

    @field_validator("destination_path")
    @classmethod
    def require_relative_destination(cls, value: str | None) -> str | None:
        return None if value is None else cls.require_project_relative_path(value)

    @model_validator(mode="after")
    def require_operation_fields(self) -> FileMutation:
        has_content = self.content is not None
        has_binary = self.content_base64 is not None
        if has_content and has_binary:
            raise ValueError("file mutation accepts one content encoding")
        if has_binary:
            try:
                decoded = base64.b64decode(self.content_base64, validate=True)
            except (ValueError, binascii.Error) as error:
                raise ValueError("content_base64 must be valid Base64") from error
            if len(decoded) > 2_097_152:
                raise ValueError("decoded file content exceeds 2 MiB")
        if self.operation in {
            FileMutationOperation.UPSERT,
            FileMutationOperation.CREATE,
            FileMutationOperation.UPDATE,
        } and not (has_content or has_binary):
            raise ValueError(f"{self.operation.value} requires file content")
        if self.operation in {FileMutationOperation.DELETE, FileMutationOperation.RENAME} and (
            has_content or has_binary
        ):
            raise ValueError(f"{self.operation.value} does not accept file content")
        if (
            self.operation
            in {
                FileMutationOperation.UPDATE,
                FileMutationOperation.DELETE,
                FileMutationOperation.RENAME,
            }
            and self.expected_hash is None
        ):
            raise ValueError(f"{self.operation.value} requires expected_hash")
        if self.operation is FileMutationOperation.CREATE and self.expected_hash is not None:
            raise ValueError("create does not accept expected_hash")
        if self.operation is FileMutationOperation.RENAME:
            if self.destination_path is None:
                raise ValueError("rename requires destination_path")
            if self.destination_path == self.path:
                raise ValueError("rename destination must differ from source")
        elif self.destination_path is not None:
            raise ValueError("destination_path is only valid for rename")
        return self

    def content_bytes(self) -> bytes | None:
        if self.content is not None:
            return self.content.encode("utf-8")
        if self.content_base64 is not None:
            return base64.b64decode(self.content_base64, validate=True)
        return None


class ChangesetProposal(ContractModel):
    task_id: UUID
    files: tuple[FileMutation, ...] = Field(min_length=1, max_length=25)
    expected_workspace_revision: int | None = Field(default=None, ge=0)
    reason: str = Field(min_length=1, max_length=10_000)
    idempotency_key: str = Field(min_length=1, max_length=255)


class EventEnvelopeModel(ContractModel):
    id: UUID
    cursor: int = Field(ge=1)
    run_id: UUID | None
    project_id: UUID | None
    conversation_id: UUID | None
    task_id: UUID | None
    version_id: UUID | None
    task_sequence: int | None = Field(ge=1)
    event_type: str = Field(min_length=1)
    visibility: EventVisibilityModel
    message: str
    payload: dict[str, Any]
    schema_version: int = Field(ge=1)
    created_at: datetime


class EventStreamStateModel(ContractModel):
    ledger_id: UUID
    oldest_cursor: int = Field(ge=0)
    latest_cursor: int = Field(ge=0)

    @model_validator(mode="after")
    def require_ordered_bounds(self) -> EventStreamStateModel:
        empty_bounds_mismatch = (self.oldest_cursor == 0) != (self.latest_cursor == 0)
        if self.oldest_cursor > self.latest_cursor or empty_bounds_mismatch:
            raise ValueError("event cursor bounds are invalid")
        return self


class ErrorModel(ContractModel):
    code: ErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


def _mutable_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _mutable_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mutable_json(item) for item in value]
    return value


def _validate_memory_snapshot_pair(snapshot_id: UUID | None, snapshot_hash: str | None) -> None:
    _validate_binding_pair(snapshot_id, snapshot_hash, "memory Snapshot")


def _validate_binding_pair(
    identity: UUID | None,
    content_hash: str | None,
    label: str,
) -> None:
    if (identity is None) != (content_hash is None):
        raise ValueError(f"{label} ID and hash must be both present")
