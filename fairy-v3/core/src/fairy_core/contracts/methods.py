from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, Field

from fairy_core.contracts.approvals import ApprovalDecisionInput, ApprovalListInput
from fairy_core.contracts.browser import (
    BrowserActionInput,
    BrowserActionResultModel,
    BrowserProfileModel,
    BrowserSessionIdInput,
    BrowserSessionListInput,
    BrowserSessionModel,
    BrowserSessionPageModel,
    BrowserSessionStartInput,
    BrowserSnapshotInput,
    BrowserSnapshotModel,
    BrowserTabIdInput,
    BrowserTabOpenInput,
    BrowserWorkerHealthModel,
)
from fairy_core.contracts.capabilities import CapabilityManifestModel
from fairy_core.contracts.extensions import (
    ExtensionCatalogPageModel,
    McpPresetInstallInput,
    McpServerAcceptInput,
    McpServerConfigureInput,
    McpServerDeleteInput,
    McpServerDeleteResult,
    McpServerDiscoverInput,
    McpServerModel,
    McpServerPageModel,
    McpServerSetEnabledInput,
    SkillCreateInput,
    SkillImportInspectInput,
    SkillImportInspectionModel,
    SkillImportInstallInput,
    SkillInstallInput,
    SkillPageModel,
    SkillRemoveInput,
    SkillRemoveResult,
    SkillSetEnabledInput,
    SkillUpdateInput,
)
from fairy_core.contracts.files import (
    AssetSetCreateInput,
    AssetSetModel,
    AssetSetPageModel,
    FileCompareInput,
    FileCompareResultModel,
    FileDescriptorModel,
    FilePresentationResultModel,
    FilePresentInput,
    FileRenderJobCancelInput,
    FileSetGetInput,
    FileSetModel,
    FileSetResolveInput,
    RendererPackInstallInput,
    RendererPackModel,
    RendererPackPageModel,
    RendererPackRemoveInput,
    RendererPackRemoveResultModel,
)
from fairy_core.contracts.history import (
    ConversationDeleteInput,
    ConversationMoveResultModel,
    ConversationMoveToProjectInput,
    ConversationUpdateInput,
    ProjectArchivedListInput,
    ProjectArchivedPageModel,
    ProjectArchiveInput,
    ProjectDeleteInput,
    ProjectMetadataUpdateInput,
    TaskArchiveInput,
    TaskMetadataUpdateInput,
    TrashItemActionInput,
    TrashItemPageModel,
    TrashListInput,
    TrashMutationResultModel,
    TrashPurgeAllInput,
    TrashPurgeResultModel,
)
from fairy_core.contracts.knowledge import (
    HarnessContextManifestModel,
    HarnessManifestGetInput,
    KnowledgeCollectionPageModel,
    KnowledgeGraphModel,
    KnowledgeItemListInput,
    KnowledgeItemPageModel,
    KnowledgeLinkPageModel,
    KnowledgeProjectInput,
    KnowledgeRevisionModel,
    KnowledgeRevisionPageModel,
    KnowledgeRevisionReadInput,
    KnowledgeSearchInput,
    KnowledgeSnapshotGetInput,
    KnowledgeSnapshotModel,
    KnowledgeSourcePageModel,
    KnowledgeSyncRunInput,
    KnowledgeSyncRunModel,
    KnowledgeSyncStartInput,
    ProjectKnowledgeOverviewModel,
)
from fairy_core.contracts.media import (
    MediaAudioGenerateInput,
    MediaGenerationJobModel,
    MediaGenerationJobPageModel,
    MediaImageGenerateInput,
    MediaJobListInput,
    MediaVideoCancelInput,
    MediaVideoJobInput,
    MediaVideoStartInput,
)
from fairy_core.contracts.model_catalog import (
    ModelCatalogListInput,
    ModelCatalogPageModel,
    ModelCatalogRefreshInput,
    ModelSelectionGetInput,
    ModelSelectionPreferenceModel,
    ModelSelectionUpdateInput,
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
    EventStreamStateModel,
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
    MemoryProposalActionInput,
    MemoryProposalListInput,
    MemoryProposalModel,
    MemoryProposalPageModel,
    MemorySearchInput,
    MemorySearchPageModel,
    MemorySettingsModel,
    MemorySettingsUpdateInput,
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
from fairy_core.contracts.obsidian import (
    ObsidianConnectorHealthModel,
    ObsidianSourceCreateInput,
    ObsidianSourceIdInput,
    ObsidianSourceListInput,
    ObsidianSourceModel,
    ObsidianSourcePageModel,
    ObsidianSourceSyncInput,
    ObsidianSyncResultModel,
    ObsidianVaultItemContentModel,
    ObsidianVaultItemPageModel,
    ObsidianVaultItemReadInput,
)
from fairy_core.contracts.planning import (
    ExecutionPlanContextModel,
    ExecutionPlanCreateInput,
    ExecutionPlanIdInput,
)
from fairy_core.contracts.presentation import (
    AnnotationDocumentModel,
    AnnotationListInput,
    AnnotationResultModel,
    AnnotationUpdateInput,
    EditRecipeApplyInput,
    EditRecipeCreateInput,
    EditRecipeIdInput,
    EditRecipeModel,
    EditRecipeUpdateInput,
    SelectionCreateInput,
    SelectionReferenceModel,
)
from fairy_core.contracts.realtime import (
    GameMemoryDeleteResult,
    GameMemoryDigestModel,
    GameMemoryIdInput,
    GameMemoryListInput,
    GameMemoryPageModel,
    GameMemorySaveInput,
    RealtimeSessionIdInput,
    RealtimeSessionListInput,
    RealtimeSessionModel,
    RealtimeSessionPageModel,
    RealtimeSessionReportInput,
    RealtimeSessionStartInput,
    RealtimeSessionStopInput,
)
from fairy_core.contracts.transcript import MessagePageModel
from fairy_core.contracts.turn_trace import TurnTraceModel
from fairy_core.contracts.voice_sessions import (
    VoiceSessionIdInput,
    VoiceSessionModel,
    VoiceSessionStartInput,
)
from fairy_core.contracts.workspaces import (
    FileReadSessionModel,
    WorkspaceExportInput,
    WorkspaceExportModel,
    WorkspaceFileContentModel,
    WorkspaceFileMutateInput,
    WorkspaceFileMutationResultModel,
    WorkspaceFilePageModel,
    WorkspaceFileReadInput,
    WorkspaceFileStreamInput,
    WorkspaceIdInput,
    WorkspaceModel,
    WorkspaceVersionInput,
)
from fairy_core.system_actions.models import SystemActionExecution, SystemActionRequest


class EmptyInput(ContractModel):
    pass


class EventSubscribeInput(ContractModel):
    cursor: int = Field(default=0, ge=0)


class EventListInput(EventSubscribeInput):
    limit: int = Field(default=500, ge=1, le=2_000)


class EventPageModel(ContractModel):
    items: tuple[EventEnvelopeModel, ...]
    next_cursor: int = Field(ge=0)


class CoreMethodTransport(StrEnum):
    LOCAL_ONLY = "local_only"
    LOCAL_AND_CLOUD = "local_and_cloud"


@dataclass(frozen=True, slots=True)
class CoreMethod:
    name: str
    request_model: type[BaseModel]
    response_model: type[BaseModel]
    transport: CoreMethodTransport = CoreMethodTransport.LOCAL_AND_CLOUD


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
        "browser.actions.execute": CoreMethod(
            "browser.actions.execute",
            BrowserActionInput,
            BrowserActionResultModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "browser.health": CoreMethod(
            "browser.health",
            EmptyInput,
            BrowserWorkerHealthModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "browser.profile.get": CoreMethod(
            "browser.profile.get",
            EmptyInput,
            BrowserProfileModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "browser.sessions.get": CoreMethod(
            "browser.sessions.get",
            BrowserSessionIdInput,
            BrowserSessionModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "browser.sessions.list": CoreMethod(
            "browser.sessions.list",
            BrowserSessionListInput,
            BrowserSessionPageModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "browser.sessions.resume": CoreMethod(
            "browser.sessions.resume",
            BrowserSessionIdInput,
            BrowserSessionModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "browser.sessions.start": CoreMethod(
            "browser.sessions.start",
            BrowserSessionStartInput,
            BrowserSessionModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "browser.sessions.stop": CoreMethod(
            "browser.sessions.stop",
            BrowserSessionIdInput,
            BrowserSessionModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "browser.snapshots.get": CoreMethod(
            "browser.snapshots.get",
            BrowserSnapshotInput,
            BrowserSnapshotModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "browser.tabs.close": CoreMethod(
            "browser.tabs.close",
            BrowserTabIdInput,
            BrowserSessionModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "browser.tabs.open": CoreMethod(
            "browser.tabs.open",
            BrowserTabOpenInput,
            BrowserSessionModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "browser.tabs.select": CoreMethod(
            "browser.tabs.select",
            BrowserTabIdInput,
            BrowserSessionModel,
            CoreMethodTransport.LOCAL_ONLY,
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
        "assistant.turns.trace.list": CoreMethod(
            "assistant.turns.trace.list",
            AssistantTurnIdInput,
            TurnTraceModel,
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
        "events.list": CoreMethod(
            "events.list",
            EventListInput,
            EventPageModel,
        ),
        "events.state": CoreMethod(
            "events.state",
            EmptyInput,
            EventStreamStateModel,
        ),
        "execution_plans.create": CoreMethod(
            "execution_plans.create",
            ExecutionPlanCreateInput,
            ExecutionPlanContextModel,
        ),
        "execution_plans.get": CoreMethod(
            "execution_plans.get",
            ExecutionPlanIdInput,
            ExecutionPlanContextModel,
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
        "memory.proposals.accept": CoreMethod(
            "memory.proposals.accept",
            MemoryProposalActionInput,
            MemoryProposalModel,
        ),
        "memory.proposals.list": CoreMethod(
            "memory.proposals.list",
            MemoryProposalListInput,
            MemoryProposalPageModel,
        ),
        "memory.proposals.reject": CoreMethod(
            "memory.proposals.reject",
            MemoryProposalActionInput,
            MemoryProposalModel,
        ),
        "memory.search": CoreMethod(
            "memory.search",
            MemorySearchInput,
            MemorySearchPageModel,
        ),
        "memory.settings.get": CoreMethod(
            "memory.settings.get",
            EmptyInput,
            MemorySettingsModel,
        ),
        "memory.settings.update": CoreMethod(
            "memory.settings.update",
            MemorySettingsUpdateInput,
            MemorySettingsModel,
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
        "media.audio.generate": CoreMethod(
            "media.audio.generate",
            MediaAudioGenerateInput,
            MediaGenerationJobModel,
        ),
        "media.images.generate": CoreMethod(
            "media.images.generate",
            MediaImageGenerateInput,
            MediaGenerationJobModel,
        ),
        "media.jobs.list": CoreMethod(
            "media.jobs.list",
            MediaJobListInput,
            MediaGenerationJobPageModel,
        ),
        "media.videos.cancel": CoreMethod(
            "media.videos.cancel",
            MediaVideoCancelInput,
            MediaGenerationJobModel,
        ),
        "media.videos.get": CoreMethod(
            "media.videos.get",
            MediaVideoJobInput,
            MediaGenerationJobModel,
        ),
        "media.videos.start": CoreMethod(
            "media.videos.start",
            MediaVideoStartInput,
            MediaGenerationJobModel,
        ),
        "models.catalog.list": CoreMethod(
            "models.catalog.list",
            ModelCatalogListInput,
            ModelCatalogPageModel,
        ),
        "models.catalog.refresh": CoreMethod(
            "models.catalog.refresh",
            ModelCatalogRefreshInput,
            ModelCatalogPageModel,
        ),
        "models.selection.get": CoreMethod(
            "models.selection.get",
            ModelSelectionGetInput,
            ModelSelectionPreferenceModel,
        ),
        "models.selection.update": CoreMethod(
            "models.selection.update",
            ModelSelectionUpdateInput,
            ModelSelectionPreferenceModel,
        ),
        "obsidian.health.get": CoreMethod(
            "obsidian.health.get",
            EmptyInput,
            ObsidianConnectorHealthModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "obsidian.sources.create": CoreMethod(
            "obsidian.sources.create",
            ObsidianSourceCreateInput,
            ObsidianSourceModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "obsidian.sources.items.list": CoreMethod(
            "obsidian.sources.items.list",
            ObsidianSourceIdInput,
            ObsidianVaultItemPageModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "obsidian.sources.items.read": CoreMethod(
            "obsidian.sources.items.read",
            ObsidianVaultItemReadInput,
            ObsidianVaultItemContentModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "obsidian.sources.list": CoreMethod(
            "obsidian.sources.list",
            ObsidianSourceListInput,
            ObsidianSourcePageModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "obsidian.sync.start": CoreMethod(
            "obsidian.sync.start",
            ObsidianSourceSyncInput,
            ObsidianSyncResultModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "knowledge.graph.get": CoreMethod(
            "knowledge.graph.get",
            KnowledgeProjectInput,
            KnowledgeGraphModel,
        ),
        "knowledge.items.list": CoreMethod(
            "knowledge.items.list",
            KnowledgeItemListInput,
            KnowledgeItemPageModel,
        ),
        "knowledge.projects.overview": CoreMethod(
            "knowledge.projects.overview",
            KnowledgeProjectInput,
            ProjectKnowledgeOverviewModel,
        ),
        "knowledge.sources.list": CoreMethod(
            "knowledge.sources.list",
            KnowledgeProjectInput,
            KnowledgeSourcePageModel,
        ),
        "knowledge.collections.list": CoreMethod(
            "knowledge.collections.list",
            KnowledgeProjectInput,
            KnowledgeCollectionPageModel,
        ),
        "knowledge.snapshots.get": CoreMethod(
            "knowledge.snapshots.get",
            KnowledgeSnapshotGetInput,
            KnowledgeSnapshotModel,
        ),
        "harness.manifests.get": CoreMethod(
            "harness.manifests.get",
            HarnessManifestGetInput,
            HarnessContextManifestModel,
        ),
        "knowledge.search": CoreMethod(
            "knowledge.search",
            KnowledgeSearchInput,
            KnowledgeRevisionPageModel,
        ),
        "knowledge.read": CoreMethod(
            "knowledge.read",
            KnowledgeRevisionReadInput,
            KnowledgeRevisionModel,
        ),
        "knowledge.links": CoreMethod(
            "knowledge.links",
            KnowledgeRevisionReadInput,
            KnowledgeLinkPageModel,
        ),
        "knowledge.sync.start": CoreMethod(
            "knowledge.sync.start",
            KnowledgeSyncStartInput,
            KnowledgeSyncRunModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "knowledge.sync.get": CoreMethod(
            "knowledge.sync.get",
            KnowledgeSyncRunInput,
            KnowledgeSyncRunModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "knowledge.sync.cancel": CoreMethod(
            "knowledge.sync.cancel",
            KnowledgeSyncRunInput,
            KnowledgeSyncRunModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "projects.create": CoreMethod(
            "projects.create",
            ProjectCreate,
            ProjectContextModel,
        ),
        "projects.archive": CoreMethod(
            "projects.archive",
            ProjectArchiveInput,
            ProjectModel,
        ),
        "projects.archived.delete": CoreMethod(
            "projects.archived.delete",
            ProjectDeleteInput,
            ProjectModel,
        ),
        "projects.archived.list": CoreMethod(
            "projects.archived.list",
            ProjectArchivedListInput,
            ProjectArchivedPageModel,
        ),
        "projects.archived.restore": CoreMethod(
            "projects.archived.restore",
            ProjectArchiveInput,
            ProjectModel,
        ),
        "projects.delete": CoreMethod(
            "projects.delete",
            ProjectDeleteInput,
            ProjectModel,
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
        "projects.update_metadata": CoreMethod(
            "projects.update_metadata",
            ProjectMetadataUpdateInput,
            ProjectModel,
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
        "trash.items.list": CoreMethod(
            "trash.items.list",
            TrashListInput,
            TrashItemPageModel,
        ),
        "trash.items.purge": CoreMethod(
            "trash.items.purge",
            TrashItemActionInput,
            TrashMutationResultModel,
        ),
        "trash.items.purge_all": CoreMethod(
            "trash.items.purge_all",
            TrashPurgeAllInput,
            TrashPurgeResultModel,
        ),
        "trash.items.restore": CoreMethod(
            "trash.items.restore",
            TrashItemActionInput,
            TrashMutationResultModel,
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
        "extensions.catalog.list": CoreMethod(
            "extensions.catalog.list",
            EmptyInput,
            ExtensionCatalogPageModel,
        ),
        "skills.install": CoreMethod(
            "skills.install",
            SkillInstallInput,
            SkillPageModel,
        ),
        "skills.import.inspect": CoreMethod(
            "skills.import.inspect",
            SkillImportInspectInput,
            SkillImportInspectionModel,
        ),
        "skills.import.install": CoreMethod(
            "skills.import.install",
            SkillImportInstallInput,
            SkillPageModel,
        ),
        "skills.create": CoreMethod(
            "skills.create",
            SkillCreateInput,
            SkillPageModel,
        ),
        "skills.list": CoreMethod(
            "skills.list",
            EmptyInput,
            SkillPageModel,
        ),
        "skills.remove": CoreMethod(
            "skills.remove",
            SkillRemoveInput,
            SkillRemoveResult,
        ),
        "skills.set_enabled": CoreMethod(
            "skills.set_enabled",
            SkillSetEnabledInput,
            SkillPageModel,
        ),
        "skills.update": CoreMethod(
            "skills.update",
            SkillUpdateInput,
            SkillPageModel,
        ),
        "mcp.presets.install": CoreMethod(
            "mcp.presets.install",
            McpPresetInstallInput,
            McpServerModel,
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
        "workspaces.files.list": CoreMethod(
            "workspaces.files.list",
            WorkspaceVersionInput,
            WorkspaceFilePageModel,
        ),
        "workspaces.files.mutate": CoreMethod(
            "workspaces.files.mutate",
            WorkspaceFileMutateInput,
            WorkspaceFileMutationResultModel,
        ),
        "workspaces.files.read": CoreMethod(
            "workspaces.files.read",
            WorkspaceFileReadInput,
            WorkspaceFileContentModel,
        ),
        "files.open_stream": CoreMethod(
            "files.open_stream",
            WorkspaceFileStreamInput,
            FileReadSessionModel,
        ),
        "files.probe": CoreMethod(
            "files.probe",
            WorkspaceFileReadInput,
            FileDescriptorModel,
        ),
        "files.present": CoreMethod(
            "files.present",
            FilePresentInput,
            FilePresentationResultModel,
        ),
        "files.compare": CoreMethod(
            "files.compare",
            FileCompareInput,
            FileCompareResultModel,
        ),
        "files.cancel": CoreMethod(
            "files.cancel",
            FileRenderJobCancelInput,
            FilePresentationResultModel,
        ),
        "asset_sets.create": CoreMethod("asset_sets.create", AssetSetCreateInput, AssetSetModel),
        "asset_sets.list": CoreMethod("asset_sets.list", WorkspaceVersionInput, AssetSetPageModel),
        "file_sets.resolve": CoreMethod(
            "file_sets.resolve",
            FileSetResolveInput,
            FileSetModel,
        ),
        "file_sets.get": CoreMethod(
            "file_sets.get",
            FileSetGetInput,
            FileSetModel,
        ),
        "renderer_packs.list": CoreMethod("renderer_packs.list", EmptyInput, RendererPackPageModel),
        "renderer_packs.health": CoreMethod(
            "renderer_packs.health", EmptyInput, RendererPackPageModel
        ),
        "renderer_packs.install": CoreMethod(
            "renderer_packs.install", RendererPackInstallInput, RendererPackModel
        ),
        "realtime.memories.delete": CoreMethod(
            "realtime.memories.delete", GameMemoryIdInput, GameMemoryDeleteResult
        ),
        "realtime.memories.list": CoreMethod(
            "realtime.memories.list", GameMemoryListInput, GameMemoryPageModel
        ),
        "realtime.memories.save": CoreMethod(
            "realtime.memories.save", GameMemorySaveInput, GameMemoryDigestModel
        ),
        "realtime.sessions.get": CoreMethod(
            "realtime.sessions.get", RealtimeSessionIdInput, RealtimeSessionModel
        ),
        "realtime.sessions.list": CoreMethod(
            "realtime.sessions.list", RealtimeSessionListInput, RealtimeSessionPageModel
        ),
        "realtime.sessions.report": CoreMethod(
            "realtime.sessions.report", RealtimeSessionReportInput, RealtimeSessionModel
        ),
        "realtime.sessions.start": CoreMethod(
            "realtime.sessions.start", RealtimeSessionStartInput, RealtimeSessionModel
        ),
        "realtime.sessions.stop": CoreMethod(
            "realtime.sessions.stop", RealtimeSessionStopInput, RealtimeSessionModel
        ),
        "renderer_packs.update": CoreMethod(
            "renderer_packs.update", RendererPackInstallInput, RendererPackModel
        ),
        "renderer_packs.remove": CoreMethod(
            "renderer_packs.remove",
            RendererPackRemoveInput,
            RendererPackRemoveResultModel,
        ),
        "annotations.list": CoreMethod(
            "annotations.list", AnnotationListInput, AnnotationResultModel
        ),
        "annotations.update": CoreMethod(
            "annotations.update", AnnotationUpdateInput, AnnotationDocumentModel
        ),
        "selections.create": CoreMethod(
            "selections.create", SelectionCreateInput, SelectionReferenceModel
        ),
        "edit_recipes.create": CoreMethod(
            "edit_recipes.create", EditRecipeCreateInput, EditRecipeModel
        ),
        "edit_recipes.update": CoreMethod(
            "edit_recipes.update", EditRecipeUpdateInput, EditRecipeModel
        ),
        "edit_recipes.apply": CoreMethod(
            "edit_recipes.apply", EditRecipeApplyInput, EditRecipeModel
        ),
        "edit_recipes.discard": CoreMethod(
            "edit_recipes.discard", EditRecipeIdInput, EditRecipeModel
        ),
        "workspaces.export": CoreMethod(
            "workspaces.export",
            WorkspaceExportInput,
            WorkspaceExportModel,
        ),
        "workspaces.get": CoreMethod(
            "workspaces.get",
            WorkspaceIdInput,
            WorkspaceModel,
        ),
        "voice.synthesize": CoreMethod(
            "voice.synthesize",
            VoiceSynthesizeInput,
            VoiceAudioModel,
        ),
        "voice.sessions.cancel": CoreMethod(
            "voice.sessions.cancel",
            VoiceSessionIdInput,
            VoiceSessionModel,
        ),
        "voice.sessions.get": CoreMethod(
            "voice.sessions.get",
            VoiceSessionIdInput,
            VoiceSessionModel,
        ),
        "voice.sessions.start": CoreMethod(
            "voice.sessions.start",
            VoiceSessionStartInput,
            VoiceSessionModel,
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
    "CoreMethodTransport",
    "EmptyInput",
    "EventListInput",
    "EventPageModel",
    "EventSubscribeInput",
]
