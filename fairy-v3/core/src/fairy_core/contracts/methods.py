from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pydantic import BaseModel, Field

from fairy_core.contracts.approvals import ApprovalDecisionInput, ApprovalListInput
from fairy_core.contracts.capabilities import CapabilityManifestModel
from fairy_core.contracts.extensions import (
    McpServerAcceptInput,
    McpServerConfigureInput,
    McpServerDeleteInput,
    McpServerDeleteResult,
    McpServerDiscoverInput,
    McpServerModel,
    McpServerPageModel,
    McpServerSetEnabledInput,
    SkillPageModel,
)
from fairy_core.contracts.history import (
    ConversationDeleteInput,
    ConversationMoveResultModel,
    ConversationMoveToProjectInput,
    ConversationUpdateInput,
    TaskArchiveInput,
    TaskMetadataUpdateInput,
)
from fairy_core.contracts.models import (
    ApprovalDecisionResultModel,
    ApprovalPageModel,
    ArtifactIdInput,
    ArtifactListInput,
    ArtifactModel,
    ArtifactPageModel,
    AssistantTurnCancelInput,
    AssistantTurnCreateInput,
    AssistantTurnIdInput,
    AssistantTurnModel,
    AssistantTurnRetryInput,
    AssistantTurnRunInput,
    AssistantTurnStartInput,
    CapabilityRequest,
    ChangesetProposal,
    CheckpointModel,
    ContractModel,
    ConversationCreate,
    ConversationIdInput,
    ConversationListInput,
    ConversationModel,
    ConversationPageModel,
    DocumentContextModel,
    DocumentDeleteInput,
    DocumentIdInput,
    DocumentImportInput,
    DocumentListInput,
    DocumentPageModel,
    DocumentSearchInput,
    DocumentSearchPageModel,
    EventEnvelopeModel,
    ExecutionSettingsModel,
    ExecutionSettingsUpdateInput,
    HealthModel,
    MemoryClaimContextModel,
    MemoryClaimGetInput,
    MemoryClaimPageModel,
    MemoryClaimPromoteInput,
    MemoryClaimQuery,
    MemoryClaimResolveInput,
    MemoryClaimSupersedeInput,
    MemoryForgetInput,
    MemoryObservationModel,
    MemoryObservationPageModel,
    MemoryObservationQuery,
    MemoryObserveInput,
    MemoryProjectionHealthInput,
    MemoryProjectionHealthModel,
    MemorySearchInput,
    MemorySearchPageModel,
    MemorySnapshotGetInput,
    MemorySnapshotModel,
    MemoryTombstoneModel,
    MessageListInput,
    PendingChangesetModel,
    PreviewContextModel,
    PreviewIdInput,
    PreviewModel,
    PreviewResolutionModel,
    PreviewResolveInput,
    PreviewStartInput,
    PreviewStopInput,
    ProjectContextModel,
    ProjectCreate,
    ProjectIdInput,
    ProjectImport,
    ProjectListInput,
    ProjectModel,
    ProjectPageModel,
    ProviderHealthInput,
    ProviderHealthPageModel,
    ProviderProfilePageModel,
    RuntimeHealthInput,
    RuntimeHealthModel,
    RuntimeIdInput,
    RuntimeModel,
    TaskContextModel,
    TaskCreate,
    TaskIdInput,
    TaskListInput,
    TaskModel,
    TaskPageModel,
    VersionAcceptInput,
    VersionIdInput,
    VersionListInput,
    VersionModel,
    VersionPageModel,
    VoiceAudioModel,
    VoiceSynthesizeInput,
    VoiceTranscribeInput,
    VoiceTranscriptModel,
)
from fairy_core.contracts.transcript import MessagePageModel
from fairy_core.system_actions.models import SystemActionExecution, SystemActionRequest


class EmptyInput(ContractModel):
    pass


class EventSubscribeInput(ContractModel):
    cursor: int = Field(default=0, ge=0)


class EventPageModel(ContractModel):
    items: tuple[EventEnvelopeModel, ...]
    next_cursor: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class CoreMethod:
    name: str
    request_model: type[BaseModel]
    response_model: type[BaseModel]


CORE_METHODS: Mapping[str, CoreMethod] = MappingProxyType(
    {
        "approvals.decide": CoreMethod(
            "approvals.decide",
            ApprovalDecisionInput,
            ApprovalDecisionResultModel,
        ),
        "approvals.list": CoreMethod(
            "approvals.list",
            ApprovalListInput,
            ApprovalPageModel,
        ),
        "artifacts.list": CoreMethod(
            "artifacts.list",
            ArtifactListInput,
            ArtifactPageModel,
        ),
        "artifacts.read": CoreMethod(
            "artifacts.read",
            ArtifactIdInput,
            ArtifactModel,
        ),
        "assistant.turns.cancel": CoreMethod(
            "assistant.turns.cancel",
            AssistantTurnCancelInput,
            AssistantTurnModel,
        ),
        "assistant.turns.create": CoreMethod(
            "assistant.turns.create",
            AssistantTurnCreateInput,
            AssistantTurnModel,
        ),
        "assistant.turns.get": CoreMethod(
            "assistant.turns.get",
            AssistantTurnIdInput,
            AssistantTurnModel,
        ),
        "assistant.turns.retry": CoreMethod(
            "assistant.turns.retry",
            AssistantTurnRetryInput,
            AssistantTurnModel,
        ),
        "assistant.turns.run": CoreMethod(
            "assistant.turns.run",
            AssistantTurnRunInput,
            AssistantTurnModel,
        ),
        "assistant.turns.start": CoreMethod(
            "assistant.turns.start",
            AssistantTurnStartInput,
            AssistantTurnModel,
        ),
        "capabilities.get": CoreMethod(
            "capabilities.get",
            CapabilityRequest,
            CapabilityManifestModel,
        ),
        "changesets.propose": CoreMethod(
            "changesets.propose",
            ChangesetProposal,
            PendingChangesetModel,
        ),
        "conversations.create": CoreMethod(
            "conversations.create",
            ConversationCreate,
            ConversationModel,
        ),
        "conversations.delete": CoreMethod(
            "conversations.delete",
            ConversationDeleteInput,
            ConversationModel,
        ),
        "conversations.get": CoreMethod(
            "conversations.get",
            ConversationIdInput,
            ConversationModel,
        ),
        "conversations.list": CoreMethod(
            "conversations.list",
            ConversationListInput,
            ConversationPageModel,
        ),
        "conversations.move_to_project": CoreMethod(
            "conversations.move_to_project",
            ConversationMoveToProjectInput,
            ConversationMoveResultModel,
        ),
        "conversations.update": CoreMethod(
            "conversations.update",
            ConversationUpdateInput,
            ConversationModel,
        ),
        "events.subscribe": CoreMethod(
            "events.subscribe",
            EventSubscribeInput,
            EventPageModel,
        ),
        "documents.delete": CoreMethod(
            "documents.delete",
            DocumentDeleteInput,
            DocumentContextModel,
        ),
        "documents.get": CoreMethod(
            "documents.get",
            DocumentIdInput,
            DocumentContextModel,
        ),
        "documents.import": CoreMethod(
            "documents.import",
            DocumentImportInput,
            DocumentContextModel,
        ),
        "documents.list": CoreMethod(
            "documents.list",
            DocumentListInput,
            DocumentPageModel,
        ),
        "documents.search": CoreMethod(
            "documents.search",
            DocumentSearchInput,
            DocumentSearchPageModel,
        ),
        "health": CoreMethod("health", EmptyInput, HealthModel),
        "memory.claims.get": CoreMethod(
            "memory.claims.get",
            MemoryClaimGetInput,
            MemoryClaimContextModel,
        ),
        "memory.claims.list": CoreMethod(
            "memory.claims.list",
            MemoryClaimQuery,
            MemoryClaimPageModel,
        ),
        "memory.claims.promote": CoreMethod(
            "memory.claims.promote",
            MemoryClaimPromoteInput,
            MemoryClaimContextModel,
        ),
        "memory.claims.resolve_conflict": CoreMethod(
            "memory.claims.resolve_conflict",
            MemoryClaimResolveInput,
            MemoryClaimContextModel,
        ),
        "memory.claims.supersede": CoreMethod(
            "memory.claims.supersede",
            MemoryClaimSupersedeInput,
            MemoryClaimContextModel,
        ),
        "memory.forget": CoreMethod(
            "memory.forget",
            MemoryForgetInput,
            MemoryTombstoneModel,
        ),
        "memory.observations.create": CoreMethod(
            "memory.observations.create",
            MemoryObserveInput,
            MemoryObservationModel,
        ),
        "memory.observations.list": CoreMethod(
            "memory.observations.list",
            MemoryObservationQuery,
            MemoryObservationPageModel,
        ),
        "memory.projection.health": CoreMethod(
            "memory.projection.health",
            MemoryProjectionHealthInput,
            MemoryProjectionHealthModel,
        ),
        "memory.search": CoreMethod(
            "memory.search",
            MemorySearchInput,
            MemorySearchPageModel,
        ),
        "memory.snapshots.get": CoreMethod(
            "memory.snapshots.get",
            MemorySnapshotGetInput,
            MemorySnapshotModel,
        ),
        "mcp.servers.accept": CoreMethod(
            "mcp.servers.accept",
            McpServerAcceptInput,
            McpServerModel,
        ),
        "mcp.servers.configure": CoreMethod(
            "mcp.servers.configure",
            McpServerConfigureInput,
            McpServerModel,
        ),
        "mcp.servers.delete": CoreMethod(
            "mcp.servers.delete",
            McpServerDeleteInput,
            McpServerDeleteResult,
        ),
        "mcp.servers.discover": CoreMethod(
            "mcp.servers.discover",
            McpServerDiscoverInput,
            McpServerModel,
        ),
        "mcp.servers.list": CoreMethod(
            "mcp.servers.list",
            EmptyInput,
            McpServerPageModel,
        ),
        "mcp.servers.set_enabled": CoreMethod(
            "mcp.servers.set_enabled",
            McpServerSetEnabledInput,
            McpServerModel,
        ),
        "messages.list": CoreMethod(
            "messages.list",
            MessageListInput,
            MessagePageModel,
        ),
        "projects.create": CoreMethod(
            "projects.create",
            ProjectCreate,
            ProjectContextModel,
        ),
        "projects.get": CoreMethod("projects.get", ProjectIdInput, ProjectModel),
        "projects.import": CoreMethod(
            "projects.import",
            ProjectImport,
            ProjectContextModel,
        ),
        "projects.list": CoreMethod(
            "projects.list",
            ProjectListInput,
            ProjectPageModel,
        ),
        "previews.get": CoreMethod(
            "previews.get",
            PreviewIdInput,
            PreviewContextModel,
        ),
        "previews.resolve": CoreMethod(
            "previews.resolve",
            PreviewResolveInput,
            PreviewResolutionModel,
        ),
        "previews.start": CoreMethod(
            "previews.start",
            PreviewStartInput,
            PreviewContextModel,
        ),
        "previews.stop": CoreMethod(
            "previews.stop",
            PreviewStopInput,
            PreviewModel,
        ),
        "permissions.get": CoreMethod(
            "permissions.get",
            EmptyInput,
            ExecutionSettingsModel,
        ),
        "permissions.update": CoreMethod(
            "permissions.update",
            ExecutionSettingsUpdateInput,
            ExecutionSettingsModel,
        ),
        "providers.health": CoreMethod(
            "providers.health",
            ProviderHealthInput,
            ProviderHealthPageModel,
        ),
        "system.actions.execute": CoreMethod(
            "system.actions.execute",
            SystemActionRequest,
            SystemActionExecution,
        ),
        "providers.list": CoreMethod(
            "providers.list",
            EmptyInput,
            ProviderProfilePageModel,
        ),
        "runtimes.get": CoreMethod(
            "runtimes.get",
            RuntimeIdInput,
            RuntimeModel,
        ),
        "runtimes.health": CoreMethod(
            "runtimes.health",
            RuntimeHealthInput,
            RuntimeHealthModel,
        ),
        "skills.list": CoreMethod(
            "skills.list",
            EmptyInput,
            SkillPageModel,
        ),
        "tasks.archive": CoreMethod("tasks.archive", TaskArchiveInput, TaskModel),
        "tasks.create": CoreMethod("tasks.create", TaskCreate, TaskContextModel),
        "tasks.get": CoreMethod("tasks.get", TaskIdInput, TaskModel),
        "tasks.list": CoreMethod("tasks.list", TaskListInput, TaskPageModel),
        "tasks.review": CoreMethod("tasks.review", TaskIdInput, CheckpointModel),
        "tasks.update_metadata": CoreMethod(
            "tasks.update_metadata",
            TaskMetadataUpdateInput,
            TaskModel,
        ),
        "versions.accept": CoreMethod(
            "versions.accept",
            VersionAcceptInput,
            ProjectModel,
        ),
        "versions.discard": CoreMethod("versions.discard", TaskIdInput, TaskModel),
        "versions.get": CoreMethod("versions.get", VersionIdInput, VersionModel),
        "versions.list": CoreMethod(
            "versions.list",
            VersionListInput,
            VersionPageModel,
        ),
        "voice.synthesize": CoreMethod(
            "voice.synthesize",
            VoiceSynthesizeInput,
            VoiceAudioModel,
        ),
        "voice.transcribe": CoreMethod(
            "voice.transcribe",
            VoiceTranscribeInput,
            VoiceTranscriptModel,
        ),
    }
)


__all__ = [
    "CORE_METHODS",
    "CoreMethod",
    "EmptyInput",
    "EventPageModel",
    "EventSubscribeInput",
]
