import type { components, operations } from "./generated/api";
import { LOCAL_ONLY_CORE_METHODS } from "./generated/rpcMethods";
export { CORE_METHOD_TRANSPORT, LOCAL_ONLY_CORE_METHODS } from "./generated/rpcMethods";
import type {
  AmbientDialogueDecision,
  AmbientDialogueEvaluateInput,
  BrowserActionInput,
  BrowserActionResult,
  BrowserProfile,
  BrowserSession,
  BrowserSessionPage,
  BrowserSessionStartInput,
  BrowserSnapshot,
  BrowserWorkerHealth,
  CompanionDigestCreateInput,
  CompanionDigestGetInput,
  CompanionDigestListInput,
  CompanionSessionDigest,
  CompanionSessionDigestPage,
  KnowledgeSyncRun,
  KnowledgeSyncRunInput,
  KnowledgeSyncStartInput,
  ObsidianConnectorHealth,
  ObsidianSource,
  ObsidianSourceCreateInput,
  ObsidianSourcePage,
  ObsidianSourceSyncInput,
  ObsidianSyncResult,
  ObsidianVaultItem,
  ObsidianVaultItemContent,
  ObsidianVaultItemPage,
  ObsidianVaultItemReadInput,
  RealtimeTranscriptAppendInput,
  RealtimeTranscriptEntry,
  RealtimeTranscriptListInput,
  RealtimeTranscriptPage,
} from "./localContracts";

export type {
  AmbientContextSnapshot,
  AmbientDialogueDecision,
  AmbientDialogueEvaluateInput,
  AmbientDialoguePreferences,
  AmbientDialogueProjection,
  AmbientDialogueState,
  AmbientSurface,
  BrowserActionInput,
  BrowserActionKind,
  BrowserActionResult,
  BrowserProfile,
  BrowserProfileKind,
  BrowserSession,
  BrowserSessionPage,
  BrowserSessionStartInput,
  BrowserSessionStatus,
  BrowserSnapshot,
  BrowserTab,
  BrowserWorkerHealth,
  CompanionDigestActivity,
  CompanionDigestCreateInput,
  CompanionDigestGetInput,
  CompanionDigestListInput,
  CompanionSessionDigest,
  CompanionSessionDigestPage,
  DialogueSource,
  DialogueTrigger,
  GeneratedDialogueRequest,
  KnowledgeSyncRun,
  KnowledgeSyncRunInput,
  KnowledgeSyncStartInput,
  ObsidianConnectorHealth,
  ObsidianReadScope,
  ObsidianSource,
  ObsidianSourceCreateInput,
  ObsidianSourceMode,
  ObsidianSourcePage,
  ObsidianSourceSyncInput,
  ObsidianSyncResult,
  ObsidianVaultItem,
  ObsidianVaultItemContent,
  ObsidianVaultItemPage,
  ObsidianVaultItemReadInput,
  RealtimeCaptionSpeaker,
  RealtimeTranscriptAppendInput,
  RealtimeTranscriptEntry,
  RealtimeTranscriptListInput,
  RealtimeTranscriptPage,
} from "./localContracts";

type Schemas = components["schemas"];

export type ApprovalDecisionInput = Schemas["ApprovalDecisionInput"];
export type ApprovalDecisionResult = Schemas["ApprovalDecisionResultModel"];
export type Approval = Schemas["ApprovalModel"];
export type ApprovalListInput = NonNullable<operations["approvals.list"]["parameters"]["query"]>;
export type ApprovalPage = Schemas["ApprovalPageModel"];
export type Artifact = Schemas["ArtifactModel"];
export type ArtifactPage = Schemas["ArtifactPageModel"];
export type AssistantTurn = Schemas["AssistantTurnModel"];
export type AssistantTurnCancelInput = Schemas["AssistantTurnCancelInput"];
export type AssistantTurnCreateInput = Schemas["AssistantTurnCreateInput"];
export type AssistantTurnRetryInput = Schemas["AssistantTurnRetryInput"];
export type AssistantTurnRunInput = Schemas["AssistantTurnRunInput"];
export type AssistantTurnStartInput = Schemas["AssistantTurnStartInput"];
export type CapabilityManifest = Schemas["CapabilityManifestModel"];
export type SlashCommandMetadata = Schemas["SlashCommandMetadataModel"];
export type ToolDefinitionMetadata = Schemas["ToolDefinitionMetadataModel"];
export type Changeset = Schemas["ChangesetModel"];
export type ChangesetProposal = Schemas["ChangesetProposal"];
export type Checkpoint = Schemas["CheckpointModel"];
export type Conversation = Schemas["ConversationModel"];
export type ConversationCreateInput = Schemas["ConversationCreate"];
export type ConversationDeleteInput = Schemas["ConversationDeleteInput"];
export type ConversationListInput = NonNullable<operations["conversations.list"]["parameters"]["query"]>;
export type ConversationMoveResult = Schemas["ConversationMoveResultModel"];
export type ConversationMoveToProjectInput = Schemas["ConversationMoveToProjectInput"];
export type ConversationPage = Schemas["ConversationPageModel"];
export type ConversationUpdateInput = Schemas["ConversationUpdateInput"];
export type Document = Schemas["ManagedDocumentModel"];
export type DocumentChunk = Schemas["DocumentChunkModel"];
export type DocumentContext = Schemas["DocumentContextModel"];
export type DocumentDeleteInput = Schemas["DocumentDeleteInput"];
export type DocumentImportInput = Schemas["DocumentImportInput"];
export type DocumentListInput = NonNullable<operations["documents.list"]["parameters"]["query"]>;
export type DocumentPage = Schemas["DocumentPageModel"];
export type DocumentRevision = Schemas["DocumentRevisionModel"];
export type DocumentSearchHit = Schemas["DocumentSearchHitModel"];
export type DocumentSearchInput = Schemas["DocumentSearchInput"];
export type DocumentSearchPage = Schemas["DocumentSearchPageModel"];
export type DocumentStatus = Schemas["DocumentStatus"];
export type DocumentVisibility = Schemas["DocumentVisibility"];
export type EventEnvelope = Schemas["EventEnvelopeModel"];
export type EventStreamState = Schemas["EventStreamStateModel"];
export type ExecutionSettings = Schemas["ExecutionSettingsModel"];
export type ExecutionSettingsUpdateInput = Schemas["ExecutionSettingsUpdateInput"];
export type ExecutionPlan = Schemas["ExecutionPlanModel"];
export type ExecutionPlanContext = Schemas["ExecutionPlanContextModel"];
export type ExecutionPlanCreateInput = Schemas["ExecutionPlanCreateInput"];
export type TaskStep = Schemas["TaskStepModel"];
export type TraceStep = Schemas["TraceStepModel"];
export type TraceStepKind = Schemas["TraceStepKind"];
export type TraceStepStatus = Schemas["TraceStepStatus"];
export type TraceVisibility = Schemas["TraceVisibility"];
export type TurnTrace = Schemas["TurnTraceModel"];
export type Health = Schemas["HealthModel"];
export type KnowledgeGraph = Schemas["KnowledgeGraphModel"];
export type KnowledgeGraphEdge = Schemas["KnowledgeGraphEdgeModel"];
export type KnowledgeGraphNode = Schemas["KnowledgeGraphNodeModel"];
export type KnowledgeItem = Schemas["KnowledgeItemModel"];
export type KnowledgeItemPage = Schemas["KnowledgeItemPageModel"];
export type KnowledgeItemListInput = NonNullable<operations["knowledge.items.list"]["parameters"]["query"]> & {
  project_id: string;
};
export type HarnessContextManifest = Schemas["HarnessContextManifestModel"];
export type KnowledgeCollection = Schemas["KnowledgeCollectionModel"];
export type KnowledgeCollectionPage = Schemas["KnowledgeCollectionPageModel"];
export type KnowledgeLinkPage = Schemas["KnowledgeLinkPageModel"];
export type KnowledgeRevision = Schemas["KnowledgeRevisionModel"];
export type KnowledgeRevisionPage = Schemas["KnowledgeRevisionPageModel"];
export type KnowledgeSnapshot = Schemas["KnowledgeSnapshotModel"];
export type KnowledgeSource = Schemas["KnowledgeSourceModel"];
export type KnowledgeSourcePage = Schemas["KnowledgeSourcePageModel"];
export type ProjectKnowledgeOverview = Schemas["ProjectKnowledgeOverviewModel"];
export type MemoryClaim = Schemas["MemoryClaimModel"];
export type MemoryClaimContext = Schemas["MemoryClaimContextModel"];
export type MemoryClaimPage = Schemas["MemoryClaimPageModel"];
export type MemoryClaimPromoteInput = Schemas["MemoryClaimPromoteInput"];
export type MemoryClaimResolveInput = Schemas["MemoryClaimResolveInput"];
export type MemoryClaimRevision = Schemas["MemoryClaimRevisionModel"];
export type MemoryClaimSupersedeInput = Schemas["MemoryClaimSupersedeInput"];
export type MemoryForgetInput = Schemas["MemoryForgetInput"];
export type MemoryNamespace = Schemas["MemoryNamespace"];
export type MemoryObservation = Schemas["MemoryObservationModel"];
export type MemoryObservationPage = Schemas["MemoryObservationPageModel"];
export type MemoryObserveInput = Schemas["MemoryObserveInput"];
export type MemoryProjectionHealth = Schemas["MemoryProjectionHealthModel"];
export type MemoryProposal = Schemas["MemoryProposalModel"];
export type MemoryProposalActionInput = Schemas["MemoryProposalActionInput"];
export type MemoryProposalPage = Schemas["MemoryProposalPageModel"];
export type MemorySearchHit = Schemas["MemorySearchHitModel"];
export type MemorySearchInput = operations["memory.search"]["parameters"]["query"];
export type MemorySearchPage = Schemas["MemorySearchPageModel"];
export type MemorySnapshot = Schemas["MemorySnapshotModel"];
export type MemorySettings = Schemas["MemorySettingsModel"];
export type MemorySettingsUpdateInput = Schemas["MemorySettingsUpdateInput"];
export type MemoryTombstone = Schemas["MemoryTombstoneModel"];
export type McpServer = Schemas["McpServerModel"];
export type McpPresetInstallInput = Schemas["McpPresetInstallInput"];
export type McpServerAcceptInput = Schemas["McpServerAcceptInput"];
export type McpServerConfigureInput = Schemas["McpServerConfigureInput"];
export type McpServerDeleteInput = Schemas["McpServerDeleteInput"];
export type McpServerDeleteResult = Schemas["McpServerDeleteResult"];
export type McpServerDiscoverInput = Schemas["McpServerDiscoverInput"];
export type McpServerPage = Schemas["McpServerPageModel"];
export type McpServerSetEnabledInput = Schemas["McpServerSetEnabledInput"];
export type McpToolPolicyInput = Schemas["McpToolPolicyInput"];
export type NativeMessage = Schemas["MessageModel"];
export type ImportedMessage = Schemas["ImportedMessageModel"];
export type ConversationTranscriptItem = NativeMessage | ImportedMessage;
export type Message = ConversationTranscriptItem;
export type MessageListInput = NonNullable<operations["messages.list"]["parameters"]["query"]>;
export type MessagePage = Schemas["MessagePageModel"];
export type MediaAudioGenerateInput = Schemas["MediaAudioGenerateInput"];
export type MediaGenerationJob = Schemas["MediaGenerationJobModel"];
export type MediaGenerationJobPage = Schemas["MediaGenerationJobPageModel"];
export type MediaImageGenerateInput = Schemas["MediaImageGenerateInput"];
export type MediaVideoCancelInput = Schemas["MediaVideoCancelInput"];
export type MediaVideoStartInput = Schemas["MediaVideoStartInput"];
export type ModelCatalogEntry = Schemas["ModelCatalogEntryModel"];
export type ModelCatalogPage = Schemas["ModelCatalogPageModel"];
export type ModelEndpointKind = Schemas["ModelEndpointKind"];
export type ModelSelectionPreference = Schemas["ModelSelectionPreferenceModel"];
export type ModelSelectionUpdateInput = Schemas["ModelSelectionUpdateInput"];
export type ProviderAccount = Schemas["ProviderAccountModel"];
export type PendingChangeset = Schemas["PendingChangesetModel"];
export type Project = Schemas["ProjectModel"];
export type ProjectContext = Schemas["ProjectContextModel"];
export type ProjectCreateInput = Schemas["ProjectCreate"];
export type ProjectMetadataUpdateInput = Schemas["ProjectMetadataUpdateInput"];
export type ProjectArchiveInput = Schemas["ProjectArchiveInput"];
export type ProjectDeleteInput = Schemas["ProjectDeleteInput"];
export type ProjectArchivedListInput = NonNullable<
  operations["projects.archived.list"]["parameters"]["query"]
>;
export type ProjectArchivedItem = Schemas["ProjectArchivedItemModel"];
export type ProjectArchivedPage = Schemas["ProjectArchivedPageModel"];
export type ProjectImportInput = Schemas["ProjectImport"];
export type ProjectListInput = NonNullable<operations["projects.list"]["parameters"]["query"]>;
export type ProjectPage = Schemas["ProjectPageModel"];
export type Preview = Schemas["PreviewModel"];
export type PreviewActivateInput = Schemas["PreviewActivateInput"];
export type PreviewActivation = Schemas["PreviewActivationModel"];
export type PreviewContext = Schemas["PreviewContextModel"];
export type PreviewResolveInput = operations["previews.resolve"]["parameters"]["query"];
export type PreviewResolution = Schemas["PreviewResolutionModel"];
export type PreviewStartInput = Schemas["PreviewStartInput"];
export type PreviewStopInput = Schemas["PreviewStopInput"];
export type ProviderHealth = Schemas["ProviderHealthModel"];
export type ProviderHealthInput = NonNullable<operations["providers.health"]["parameters"]["query"]>;
export type ProviderHealthPage = Schemas["ProviderHealthPageModel"];
export type ProviderProfile = Schemas["ProviderProfileModel"];
export type ProviderProfilePage = Schemas["ProviderProfilePageModel"];
export type Runtime = Schemas["RuntimeModel"];
export type RuntimeHealth = Schemas["RuntimeHealthModel"];
export type RealtimeSession = Schemas["RealtimeSessionModel"];
export type RealtimeSessionStartInput = Schemas["RealtimeSessionStartInput"];
export type RealtimeSessionReportInput = Schemas["RealtimeSessionReportInput"];
export type RealtimeSessionStopInput = Schemas["RealtimeSessionStopInput"];
export type RealtimeSessionPage = Schemas["RealtimeSessionPageModel"];
export type RealtimeSessionStatus = Schemas["RealtimeSessionStatus"];
export type RealtimeAssistanceStatus =
  | "queued"
  | "running"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "cancelled";
export interface RealtimeAssistance {
  id: string;
  session_id: string;
  conversation_id: string;
  request_id: string;
  segment_id: string;
  context_epoch: number;
  question: string;
  activity_profile: string;
  application_title: string | null;
  observed_facts: string[];
  allow_network: boolean;
  locale: string;
  status: RealtimeAssistanceStatus;
  task_id: string | null;
  turn_id: string | null;
  message_id: string | null;
  spoken_summary: string | null;
  display_markdown: string | null;
  citations: Array<{ title: string; url: string }>;
  freshness: string | null;
  requires_user_confirmation: boolean;
  error_code: string | null;
  created_at: string;
  updated_at: string;
  revision: number;
}
export interface RealtimeAssistanceGetInput {
  session_id: string;
  request_id: string;
}
export interface RealtimeAssistanceRequestInput extends RealtimeAssistanceGetInput {
  conversation_id: string;
  segment_id: string;
  context_epoch: number;
  question: string;
  activity_profile: string;
  application_title?: string | null;
  observed_facts?: string[];
  allow_network?: boolean;
  locale?: string;
}
export interface RealtimeAssistanceCancelInput extends RealtimeAssistanceGetInput {
  expected_revision: number;
}
export type RealtimeActivityProfile = "auto" | "game" | "focus";
export type RealtimeInteractionIntensity = "quiet" | "standard" | "active";
export interface RealtimePersonaSnapshotInput {
  locale?: string;
  activity_profile?: RealtimeActivityProfile;
  interaction_intensity?: RealtimeInteractionIntensity;
  current_goal?: string | null;
  subject_title?: string | null;
  recent_progress?: string | null;
}
export interface RealtimePersonaSnapshot {
  schema_version: number;
  persona_digest: string;
  authority_version: string;
  locale: string;
  activity_profile: RealtimeActivityProfile;
  interaction_intensity: RealtimeInteractionIntensity;
  identity: {
    name: string;
    role: string;
  };
  relationship: {
    user_has_final_authority: boolean;
    protect_privacy_time_and_work: boolean;
  };
  speech: {
    lead_with_conclusion: boolean;
    dry_humour: string;
    use_master: string;
    no_customer_service_filler: boolean;
    no_empty_praise: boolean;
  };
  realtime_policy: {
    proactive_allowed: boolean;
    max_spoken_sentences: number;
    grounding_required: boolean;
    never_claim_unobserved_action: boolean;
  };
  short_memory: {
    current_goal: string | null;
    subject_title: string | null;
    recent_progress: string | null;
  };
}
export type GameMemoryDigest = Schemas["GameMemoryDigestModel"];
export type GameMemorySaveInput = Schemas["GameMemorySaveInput"];
export type GameMemoryPage = Schemas["GameMemoryPageModel"];
export type GameMemoryDeleteResult = Schemas["GameMemoryDeleteResult"];
export type Skill = Schemas["SkillModel"];
export type SkillPage = Schemas["SkillPageModel"];
export type ExtensionCatalogEntry = Schemas["ExtensionCatalogEntryModel"];
export type ExtensionCatalogPage = Schemas["ExtensionCatalogPageModel"];
export type SkillInstallInput = Schemas["SkillInstallInput"];
export type SkillImportInspectInput = Schemas["SkillImportInspectInput"];
export type SkillImportInspection = Schemas["SkillImportInspectionModel"];
export type SkillImportInstallInput = Schemas["SkillImportInstallInput"];
export type SkillCreateInput = Schemas["SkillCreateInput"];
export type SkillUpdateInput = Schemas["SkillUpdateInput"];
export type SkillSetEnabledInput = Schemas["SkillSetEnabledInput"];
export type SkillRemoveInput = Schemas["SkillRemoveInput"];
export type SkillRemoveResult = Schemas["SkillRemoveResult"];
export type SystemActionExecution = Schemas["SystemActionExecution"];
export type SystemActionRequest = Schemas["SystemActionRequest"];
export type SystemAction = SystemActionRequest["action"];
export type SystemSettings = Schemas["SystemSettings"];
export type Task = Schemas["TaskModel"];
export type TaskArchiveInput = Schemas["TaskArchiveInput"];
export type TaskContext = Schemas["TaskContextModel"];
export type TaskCreateInput = Schemas["TaskCreate"];
export type TaskListInput = NonNullable<operations["tasks.list"]["parameters"]["query"]>;
export type TaskMetadataUpdateInput = Schemas["TaskMetadataUpdateInput"];
export type TaskPage = Schemas["TaskPageModel"];
export type TrashItemType = Schemas["TrashItemType"];
export type TrashItem = Schemas["TrashItemModel"];
export type TrashItemActionInput = Schemas["TrashItemActionInput"];
export type TrashItemPage = Schemas["TrashItemPageModel"];
export type TrashListInput = NonNullable<operations["trash.items.list"]["parameters"]["query"]>;
export type TrashMutationResult = Schemas["TrashMutationResultModel"];
export type TrashPurgeAllInput = Schemas["TrashPurgeAllInput"];
export type TrashPurgeResult = Schemas["TrashPurgeResultModel"];
export type Version = Schemas["VersionModel"];
export type VersionAcceptInput = Schemas["VersionAcceptInput"];
export type VersionListInput = NonNullable<operations["versions.list"]["parameters"]["query"]>;
export type VersionPage = Schemas["VersionPageModel"];
export type Workspace = Schemas["WorkspaceModel"];
export type WorkspaceFile = Schemas["WorkspaceFileModel"];
export type WorkspaceFileContent = Schemas["WorkspaceFileContentModel"];
export type FileReadSession = Schemas["FileReadSessionModel"];
export type FileDescriptor = Schemas["FileDescriptorModel"];
export type FileCompareInput = Schemas["FileCompareInput"];
export type FileCompareResult = Schemas["FileCompareResultModel"];
export type FileSet = Schemas["FileSetModel"];
export type FileSetGetInput = Schemas["FileSetGetInput"];
export type FileSetResolveInput = Schemas["FileSetResolveInput"];
export type FilePresentInput = Schemas["FilePresentInput"];
export type FilePresentationResult = Schemas["FilePresentationResultModel"];
export type AssetSet = Schemas["AssetSetModel"];
export type AssetSetCreateInput = Schemas["AssetSetCreateInput"];
export type AssetSetPage = Schemas["AssetSetPageModel"];
export type FileRenderJobCancelInput = Schemas["FileRenderJobCancelInput"];
export type RendererPack = Schemas["RendererPackModel"];
export type RendererPackInstallInput = Schemas["RendererPackInstallInput"];
export type RendererPackPage = Schemas["RendererPackPageModel"];
export type RendererPackRemoveInput = Schemas["RendererPackRemoveInput"];
export type RendererPackRemoveResult = Schemas["RendererPackRemoveResultModel"];
export type AnnotationDocument = Schemas["AnnotationDocumentModel"];
export type AnnotationListInput = Schemas["AnnotationListInput"];
export type AnnotationResult = Schemas["AnnotationResultModel"];
export type AnnotationUpdateInput = Schemas["AnnotationUpdateInput"];
export type SelectionCreateInput = Schemas["SelectionCreateInput"];
export type SelectionReference = Schemas["SelectionReferenceModel"];
export type EditRecipe = Schemas["EditRecipeModel"];
export type EditRecipeCreateInput = Schemas["EditRecipeCreateInput"];
export type EditRecipeApplyInput = Schemas["EditRecipeApplyInput"];
export type EditRecipeUpdateInput = Schemas["EditRecipeUpdateInput"];
export type EditRecipeIdInput = Schemas["EditRecipeIdInput"];
export type WorkspaceFileMutateInput = Schemas["WorkspaceFileMutateInput"];
export type WorkspaceFileMutationResult = Schemas["WorkspaceFileMutationResultModel"];
export type WorkspaceFilePage = Schemas["WorkspaceFilePageModel"];
export type WorkspaceExportInput = Schemas["WorkspaceExportInput"];
export type WorkspaceExport = Schemas["WorkspaceExportModel"];
export type WorkspaceIdInput = { workspace_id: string };
export type WorkspaceVersionInput = WorkspaceIdInput & { version_id?: string | null };
export type WorkspaceFileReadInput = WorkspaceVersionInput & { path: string };
export type WorkspaceFileStreamInput = Schemas["WorkspaceFileStreamInput"];
export type VoiceAudio = Schemas["VoiceAudioModel"];
export type VoiceSession = Schemas["VoiceSessionModel"];
export interface VoiceSessionIdInput {
  session_id: string;
}
export type VoiceSessionStartInput = Schemas["VoiceSessionStartInput"];
export type VoiceSessionStatus = Schemas["VoiceSessionStatus"];
export type VoiceSynthesizeInput = Schemas["VoiceSynthesizeInput"];
export type VoiceTranscript = Schemas["VoiceTranscriptModel"];
export type VoiceTranscribeInput = Schemas["VoiceTranscribeInput"];

export interface EventBatch {
  items: EventEnvelope[];
  next_cursor: number;
}

export interface EventSubscriptionOptions {
  signal?: AbortSignal;
  pollIntervalMs?: number;
}

type EmptyParams = Record<string, never>;

export interface CoreMethodMap {
  "ambient.dialogue.evaluate": {
    params: AmbientDialogueEvaluateInput;
    result: AmbientDialogueDecision;
  };
  health: { params: EmptyParams; result: Health };
  "browser.health": { params: EmptyParams; result: BrowserWorkerHealth };
  "browser.profile.get": { params: EmptyParams; result: BrowserProfile };
  "browser.sessions.start": { params: BrowserSessionStartInput; result: BrowserSession };
  "browser.sessions.get": { params: { session_id: string }; result: BrowserSession };
  "browser.sessions.list": {
    params: {
      conversation_id?: string | null;
      task_id?: string | null;
      exact_task_scope?: boolean;
      include_terminal?: boolean;
    };
    result: BrowserSessionPage;
  };
  "browser.sessions.stop": { params: { session_id: string }; result: BrowserSession };
  "browser.sessions.resume": { params: { session_id: string }; result: BrowserSession };
  "browser.tabs.open": {
    params: { session_id: string; url?: string };
    result: BrowserSession;
  };
  "browser.tabs.select": {
    params: { session_id: string; tab_id: string };
    result: BrowserSession;
  };
  "browser.tabs.close": {
    params: { session_id: string; tab_id: string };
    result: BrowserSession;
  };
  "browser.actions.execute": { params: BrowserActionInput; result: BrowserActionResult };
  "browser.snapshots.get": {
    params: { session_id: string; tab_id: string; include_screenshot?: boolean };
    result: BrowserSnapshot;
  };
  "projects.create": { params: ProjectCreateInput; result: ProjectContext };
  "projects.import": { params: ProjectImportInput; result: ProjectContext };
  "projects.get": { params: { project_id: string }; result: Project };
  "projects.list": { params: ProjectListInput; result: ProjectPage };
  "projects.update_metadata": { params: ProjectMetadataUpdateInput; result: Project };
  "projects.archive": { params: ProjectArchiveInput; result: Project };
  "projects.delete": { params: ProjectDeleteInput; result: Project };
  "projects.archived.list": { params: ProjectArchivedListInput; result: ProjectArchivedPage };
  "projects.archived.restore": { params: ProjectArchiveInput; result: Project };
  "projects.archived.delete": { params: ProjectDeleteInput; result: Project };
  "trash.items.list": { params: TrashListInput; result: TrashItemPage };
  "trash.items.restore": { params: TrashItemActionInput; result: TrashMutationResult };
  "trash.items.purge": { params: TrashItemActionInput; result: TrashMutationResult };
  "trash.items.purge_all": { params: TrashPurgeAllInput; result: TrashPurgeResult };
  "conversations.create": { params: ConversationCreateInput; result: Conversation };
  "conversations.delete": { params: ConversationDeleteInput; result: Conversation };
  "conversations.get": { params: { conversation_id: string }; result: Conversation };
  "conversations.list": {
    params: ConversationListInput;
    result: ConversationPage;
  };
  "conversations.move_to_project": {
    params: ConversationMoveToProjectInput;
    result: ConversationMoveResult;
  };
  "conversations.update": { params: ConversationUpdateInput; result: Conversation };
  "documents.import": { params: DocumentImportInput; result: DocumentContext };
  "documents.list": { params: DocumentListInput; result: DocumentPage };
  "documents.get": {
    params: { task_id: string; document_id: string };
    result: DocumentContext;
  };
  "documents.search": { params: DocumentSearchInput; result: DocumentSearchPage };
  "documents.delete": { params: DocumentDeleteInput; result: DocumentContext };
  "knowledge.graph.get": { params: { project_id: string }; result: KnowledgeGraph };
  "knowledge.items.list": { params: KnowledgeItemListInput; result: KnowledgeItemPage };
  "knowledge.projects.overview": {
    params: { project_id: string };
    result: ProjectKnowledgeOverview;
  };
  "knowledge.sources.list": {
    params: { project_id: string };
    result: KnowledgeSourcePage;
  };
  "knowledge.collections.list": {
    params: { project_id: string };
    result: KnowledgeCollectionPage;
  };
  "knowledge.snapshots.get": {
    params: { task_id: string; snapshot_id: string };
    result: KnowledgeSnapshot;
  };
  "harness.manifests.get": {
    params: { task_id: string; manifest_id: string };
    result: HarnessContextManifest;
  };
  "knowledge.search": {
    params: { task_id: string; snapshot_id: string; query: string; limit?: number };
    result: KnowledgeRevisionPage;
  };
  "knowledge.read": {
    params: { task_id: string; snapshot_id: string; revision_id: string };
    result: KnowledgeRevision;
  };
  "knowledge.links": {
    params: { task_id: string; snapshot_id: string; revision_id: string };
    result: KnowledgeLinkPage;
  };
  "knowledge.sync.start": { params: KnowledgeSyncStartInput; result: KnowledgeSyncRun };
  "knowledge.sync.get": { params: KnowledgeSyncRunInput; result: KnowledgeSyncRun };
  "knowledge.sync.cancel": { params: KnowledgeSyncRunInput; result: KnowledgeSyncRun };
  "obsidian.health.get": { params: EmptyParams; result: ObsidianConnectorHealth };
  "obsidian.sources.create": { params: ObsidianSourceCreateInput; result: ObsidianSource };
  "obsidian.sources.list": { params: { project_id: string }; result: ObsidianSourcePage };
  "obsidian.sources.items.list": {
    params: { source_id: string };
    result: ObsidianVaultItemPage;
  };
  "obsidian.sources.items.read": {
    params: ObsidianVaultItemReadInput;
    result: ObsidianVaultItemContent;
  };
  "obsidian.sync.start": { params: ObsidianSourceSyncInput; result: ObsidianSyncResult };
  "tasks.archive": { params: TaskArchiveInput; result: Task };
  "tasks.create": { params: TaskCreateInput; result: TaskContext };
  "tasks.get": { params: { task_id: string }; result: Task };
  "tasks.list": { params: TaskListInput; result: TaskPage };
  "tasks.review": { params: { task_id: string }; result: Checkpoint };
  "tasks.update_metadata": { params: TaskMetadataUpdateInput; result: Task };
  "execution_plans.create": {
    params: ExecutionPlanCreateInput;
    result: ExecutionPlanContext;
  };
  "execution_plans.get": {
    params: { task_id: string };
    result: ExecutionPlanContext;
  };
  "changesets.propose": { params: ChangesetProposal; result: PendingChangeset };
  "approvals.decide": { params: ApprovalDecisionInput; result: ApprovalDecisionResult };
  "approvals.list": { params: ApprovalListInput; result: ApprovalPage };
  "versions.get": { params: { version_id: string }; result: Version };
  "versions.list": { params: VersionListInput; result: VersionPage };
  "versions.accept": { params: VersionAcceptInput; result: Project };
  "versions.discard": { params: { task_id: string }; result: Task };
  "capabilities.get": { params: EmptyParams; result: CapabilityManifest };
  "permissions.get": { params: EmptyParams; result: ExecutionSettings };
  "permissions.update": {
    params: ExecutionSettingsUpdateInput;
    result: ExecutionSettings;
  };
  "providers.list": { params: EmptyParams; result: ProviderProfilePage };
  "providers.health": { params: ProviderHealthInput; result: ProviderHealthPage };
  "models.catalog.list": { params: EmptyParams; result: ModelCatalogPage };
  "models.catalog.refresh": { params: EmptyParams; result: ModelCatalogPage };
  "models.selection.get": {
    params: EmptyParams;
    result: ModelSelectionPreference;
  };
  "models.selection.update": {
    params: ModelSelectionUpdateInput;
    result: ModelSelectionPreference;
  };
  "media.images.generate": {
    params: MediaImageGenerateInput;
    result: MediaGenerationJob;
  };
  "media.jobs.list": {
    params: { task_id: string };
    result: MediaGenerationJobPage;
  };
  "media.audio.generate": {
    params: MediaAudioGenerateInput;
    result: MediaGenerationJob;
  };
  "media.videos.start": {
    params: MediaVideoStartInput;
    result: MediaGenerationJob;
  };
  "media.videos.get": {
    params: { job_id: string };
    result: MediaGenerationJob;
  };
  "media.videos.cancel": {
    params: MediaVideoCancelInput;
    result: MediaGenerationJob;
  };
  "extensions.catalog.list": { params: EmptyParams; result: ExtensionCatalogPage };
  "skills.install": { params: SkillInstallInput; result: SkillPage };
  "skills.import.inspect": { params: SkillImportInspectInput; result: SkillImportInspection };
  "skills.import.install": { params: SkillImportInstallInput; result: SkillPage };
  "skills.create": { params: SkillCreateInput; result: SkillPage };
  "skills.list": { params: EmptyParams; result: SkillPage };
  "skills.remove": { params: SkillRemoveInput; result: SkillRemoveResult };
  "skills.set_enabled": { params: SkillSetEnabledInput; result: SkillPage };
  "skills.update": { params: SkillUpdateInput; result: SkillPage };
  "mcp.servers.list": { params: EmptyParams; result: McpServerPage };
  "mcp.presets.install": { params: McpPresetInstallInput; result: McpServer };
  "mcp.servers.configure": {
    params: McpServerConfigureInput;
    result: McpServer;
  };
  "mcp.servers.discover": {
    params: McpServerDiscoverInput;
    result: McpServer;
  };
  "mcp.servers.accept": {
    params: McpServerAcceptInput;
    result: McpServer;
  };
  "mcp.servers.set_enabled": {
    params: McpServerSetEnabledInput;
    result: McpServer;
  };
  "mcp.servers.delete": {
    params: McpServerDeleteInput;
    result: McpServerDeleteResult;
  };
  "runtimes.get": { params: { runtime_id: string }; result: Runtime };
  "runtimes.health": { params: { task_id: string }; result: RuntimeHealth };
  "system.actions.execute": {
    params: SystemActionRequest;
    result: SystemActionExecution;
  };
  "previews.activate": { params: PreviewActivateInput; result: PreviewActivation };
  "previews.start": { params: PreviewStartInput; result: PreviewContext };
  "previews.get": { params: { preview_id: string }; result: PreviewContext };
  "previews.resolve": { params: PreviewResolveInput; result: PreviewResolution };
  "previews.stop": { params: PreviewStopInput; result: Preview };
  "artifacts.list": { params: { task_id: string }; result: ArtifactPage };
  "artifacts.read": { params: { artifact_id: string }; result: Artifact };
  "assistant.turns.create": {
    params: AssistantTurnCreateInput;
    result: AssistantTurn;
  };
  "assistant.turns.get": { params: { turn_id: string }; result: AssistantTurn };
  "assistant.turns.cancel": {
    params: AssistantTurnCancelInput;
    result: AssistantTurn;
  };
  "assistant.turns.run": {
    params: AssistantTurnRunInput;
    result: AssistantTurn;
  };
  "assistant.turns.start": {
    params: AssistantTurnStartInput;
    result: AssistantTurn;
  };
  "assistant.turns.retry": {
    params: AssistantTurnRetryInput;
    result: AssistantTurn;
  };
  "assistant.turns.trace.list": {
    params: { turn_id: string };
    result: TurnTrace;
  };
  "messages.list": { params: MessageListInput; result: MessagePage };
  "voice.synthesize": { params: VoiceSynthesizeInput; result: VoiceAudio };
  "voice.sessions.start": { params: VoiceSessionStartInput; result: VoiceSession };
  "voice.sessions.get": { params: VoiceSessionIdInput; result: VoiceSession };
  "voice.sessions.cancel": { params: VoiceSessionIdInput; result: VoiceSession };
  "voice.transcribe": { params: VoiceTranscribeInput; result: VoiceTranscript };
  "realtime.sessions.start": { params: RealtimeSessionStartInput; result: RealtimeSession };
  "realtime.sessions.get": { params: { session_id: string }; result: RealtimeSession };
  "realtime.sessions.list": { params: { limit?: number }; result: RealtimeSessionPage };
  "realtime.sessions.report": { params: RealtimeSessionReportInput; result: RealtimeSession };
  "realtime.sessions.stop": { params: RealtimeSessionStopInput; result: RealtimeSession };
  "realtime.assistance.get": {
    params: RealtimeAssistanceGetInput;
    result: RealtimeAssistance;
  };
  "realtime.assistance.request": {
    params: RealtimeAssistanceRequestInput;
    result: RealtimeAssistance;
  };
  "realtime.assistance.cancel": {
    params: RealtimeAssistanceCancelInput;
    result: RealtimeAssistance;
  };
  "realtime.digests.create": {
    params: CompanionDigestCreateInput;
    result: CompanionSessionDigest;
  };
  "realtime.digests.get": {
    params: CompanionDigestGetInput;
    result: CompanionSessionDigest;
  };
  "realtime.digests.list": {
    params: CompanionDigestListInput;
    result: CompanionSessionDigestPage;
  };
  "realtime.memories.save": { params: GameMemorySaveInput; result: GameMemoryDigest };
  "realtime.memories.list": { params: { limit?: number }; result: GameMemoryPage };
  "realtime.memories.delete": {
    params: { memory_id: string };
    result: GameMemoryDeleteResult;
  };
  "realtime.persona.snapshot": {
    params: RealtimePersonaSnapshotInput;
    result: RealtimePersonaSnapshot;
  };
  "realtime.transcript.append": {
    params: RealtimeTranscriptAppendInput;
    result: RealtimeTranscriptEntry;
  };
  "realtime.transcript.list": {
    params: RealtimeTranscriptListInput;
    result: RealtimeTranscriptPage;
  };
  "workspaces.get": { params: WorkspaceIdInput; result: Workspace };
  "workspaces.files.list": {
    params: WorkspaceVersionInput;
    result: WorkspaceFilePage;
  };
  "workspaces.files.read": {
    params: WorkspaceFileReadInput;
    result: WorkspaceFileContent;
  };
  "files.open_stream": {
    params: WorkspaceFileStreamInput;
    result: FileReadSession;
  };
  "files.probe": {
    params: WorkspaceFileReadInput;
    result: FileDescriptor;
  };
  "files.present": { params: FilePresentInput; result: FilePresentationResult };
  "files.compare": { params: FileCompareInput; result: FileCompareResult };
  "files.cancel": {
    params: FileRenderJobCancelInput;
    result: FilePresentationResult;
  };
  "asset_sets.create": { params: AssetSetCreateInput; result: AssetSet };
  "asset_sets.list": { params: WorkspaceVersionInput; result: AssetSetPage };
  "file_sets.get": { params: FileSetGetInput; result: FileSet };
  "file_sets.resolve": { params: FileSetResolveInput; result: FileSet };
  "renderer_packs.list": { params: EmptyParams; result: RendererPackPage };
  "renderer_packs.health": { params: EmptyParams; result: RendererPackPage };
  "renderer_packs.install": {
    params: RendererPackInstallInput;
    result: RendererPack;
  };
  "renderer_packs.update": {
    params: RendererPackInstallInput;
    result: RendererPack;
  };
  "renderer_packs.remove": {
    params: RendererPackRemoveInput;
    result: RendererPackRemoveResult;
  };
  "annotations.list": { params: AnnotationListInput; result: AnnotationResult };
  "annotations.update": {
    params: AnnotationUpdateInput;
    result: AnnotationDocument;
  };
  "selections.create": {
    params: SelectionCreateInput;
    result: SelectionReference;
  };
  "edit_recipes.create": { params: EditRecipeCreateInput; result: EditRecipe };
  "edit_recipes.update": { params: EditRecipeUpdateInput; result: EditRecipe };
  "edit_recipes.apply": { params: EditRecipeApplyInput; result: EditRecipe };
  "edit_recipes.discard": { params: EditRecipeIdInput; result: EditRecipe };
  "workspaces.files.mutate": {
    params: WorkspaceFileMutateInput;
    result: WorkspaceFileMutationResult;
  };
  "workspaces.export": {
    params: WorkspaceExportInput;
    result: WorkspaceExport;
  };
  "events.subscribe": { params: { cursor: number }; result: EventBatch };
  "events.list": {
    params: { cursor: number; limit: number };
    result: EventBatch;
  };
  "events.state": { params: EmptyParams; result: EventStreamState };
  "memory.observations.create": {
    params: MemoryObserveInput;
    result: MemoryObservation;
  };
  "memory.observations.list": {
    params: { task_id: string; namespace: MemoryNamespace };
    result: MemoryObservationPage;
  };
  "memory.claims.promote": {
    params: MemoryClaimPromoteInput;
    result: MemoryClaimContext;
  };
  "memory.claims.get": {
    params: { task_id: string; claim_id: string };
    result: MemoryClaimContext;
  };
  "memory.claims.list": {
    params: { task_id: string; namespace: MemoryNamespace };
    result: MemoryClaimPage;
  };
  "memory.claims.supersede": {
    params: MemoryClaimSupersedeInput;
    result: MemoryClaimContext;
  };
  "memory.claims.resolve_conflict": {
    params: MemoryClaimResolveInput;
    result: MemoryClaimContext;
  };
  "memory.forget": { params: MemoryForgetInput; result: MemoryTombstone };
  "memory.search": { params: MemorySearchInput; result: MemorySearchPage };
  "memory.snapshots.get": {
    params: { task_id: string; snapshot_id: string };
    result: MemorySnapshot;
  };
  "memory.projection.health": {
    params: { task_id: string };
    result: MemoryProjectionHealth;
  };
  "memory.proposals.list": {
    params: { task_id: string; limit: number };
    result: MemoryProposalPage;
  };
  "memory.proposals.accept": {
    params: MemoryProposalActionInput;
    result: MemoryProposal;
  };
  "memory.proposals.reject": {
    params: MemoryProposalActionInput;
    result: MemoryProposal;
  };
  "memory.settings.get": { params: EmptyParams; result: MemorySettings };
  "memory.settings.update": {
    params: MemorySettingsUpdateInput;
    result: MemorySettings;
  };
}

export type CoreMethodName = keyof CoreMethodMap;

export type LocalOnlyCoreMethod = (typeof LOCAL_ONLY_CORE_METHODS)[number];
export type CloudCoreMethod = Exclude<CoreMethodName, LocalOnlyCoreMethod>;

type GeneratedRpcMethod = Exclude<keyof operations, "cloud.ready" | `sync.${string}`>;
type CoreMethodContractCoverage =
  Exclude<GeneratedRpcMethod, CloudCoreMethod> extends never
    ? Exclude<CloudCoreMethod, GeneratedRpcMethod> extends never
      ? true
      : never
    : never;

export const CORE_METHOD_CONTRACT_COMPLETE: CoreMethodContractCoverage = true;
