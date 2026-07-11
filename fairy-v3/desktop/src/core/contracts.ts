import type { components, operations } from "./generated/api";

type Schemas = components["schemas"];

export type ApprovalDecisionInput = Schemas["ApprovalDecisionInput"];
export type Approval = Schemas["ApprovalModel"];
export type ApprovalListInput = NonNullable<
  operations["approvals.list"]["parameters"]["query"]
>;
export type ApprovalPage = Schemas["ApprovalPageModel"];
export type Artifact = Schemas["ArtifactModel"];
export type ArtifactPage = Schemas["ArtifactPageModel"];
export type AssistantTurn = Schemas["AssistantTurnModel"];
export type AssistantTurnCancelInput = Schemas["AssistantTurnCancelInput"];
export type AssistantTurnCreateInput = Schemas["AssistantTurnCreateInput"];
export type AssistantTurnRetryInput = Schemas["AssistantTurnRetryInput"];
export type AssistantTurnRunInput = Schemas["AssistantTurnRunInput"];
export type CapabilityManifest = Schemas["CapabilityManifestModel"];
export type Changeset = Schemas["ChangesetModel"];
export type ChangesetProposal = Schemas["ChangesetProposal"];
export type Checkpoint = Schemas["CheckpointModel"];
export type Conversation = Schemas["ConversationModel"];
export type ConversationCreateInput = Schemas["ConversationCreate"];
export type ConversationListInput = NonNullable<
  operations["conversations.list"]["parameters"]["query"]
>;
export type ConversationPage = Schemas["ConversationPageModel"];
export type Document = Schemas["ManagedDocumentModel"];
export type DocumentChunk = Schemas["DocumentChunkModel"];
export type DocumentContext = Schemas["DocumentContextModel"];
export type DocumentDeleteInput = Schemas["DocumentDeleteInput"];
export type DocumentImportInput = Schemas["DocumentImportInput"];
export type DocumentListInput = NonNullable<
  operations["documents.list"]["parameters"]["query"]
>;
export type DocumentPage = Schemas["DocumentPageModel"];
export type DocumentRevision = Schemas["DocumentRevisionModel"];
export type DocumentSearchHit = Schemas["DocumentSearchHitModel"];
export type DocumentSearchInput = Schemas["DocumentSearchInput"];
export type DocumentSearchPage = Schemas["DocumentSearchPageModel"];
export type DocumentStatus = Schemas["DocumentStatus"];
export type DocumentVisibility = Schemas["DocumentVisibility"];
export type EventEnvelope = Schemas["EventEnvelopeModel"];
export type ExecutionSettings = Schemas["ExecutionSettingsModel"];
export type ExecutionSettingsUpdateInput = Schemas["ExecutionSettingsUpdateInput"];
export type Health = Schemas["HealthModel"];
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
export type MemorySearchHit = Schemas["MemorySearchHitModel"];
export type MemorySearchInput = operations["memory.search"]["parameters"]["query"];
export type MemorySearchPage = Schemas["MemorySearchPageModel"];
export type MemorySnapshot = Schemas["MemorySnapshotModel"];
export type MemoryTombstone = Schemas["MemoryTombstoneModel"];
export type Message = Schemas["MessageModel"];
export type MessageListInput = NonNullable<
  operations["messages.list"]["parameters"]["query"]
>;
export type MessagePage = Schemas["MessagePageModel"];
export type PendingChangeset = Schemas["PendingChangesetModel"];
export type Project = Schemas["ProjectModel"];
export type ProjectContext = Schemas["ProjectContextModel"];
export type ProjectCreateInput = Schemas["ProjectCreate"];
export type ProjectImportInput = Schemas["ProjectImport"];
export type ProjectListInput = NonNullable<
  operations["projects.list"]["parameters"]["query"]
>;
export type ProjectPage = Schemas["ProjectPageModel"];
export type Preview = Schemas["PreviewModel"];
export type PreviewContext = Schemas["PreviewContextModel"];
export type PreviewResolveInput = operations["previews.resolve"]["parameters"]["query"];
export type PreviewResolution = Schemas["PreviewResolutionModel"];
export type PreviewStartInput = Schemas["PreviewStartInput"];
export type PreviewStopInput = Schemas["PreviewStopInput"];
export type ProviderHealth = Schemas["ProviderHealthModel"];
export type ProviderHealthInput = NonNullable<
  operations["providers.health"]["parameters"]["query"]
>;
export type ProviderHealthPage = Schemas["ProviderHealthPageModel"];
export type ProviderProfile = Schemas["ProviderProfileModel"];
export type ProviderProfilePage = Schemas["ProviderProfilePageModel"];
export type Runtime = Schemas["RuntimeModel"];
export type RuntimeHealth = Schemas["RuntimeHealthModel"];
export type SystemActionExecution = Schemas["SystemActionExecution"];
export type SystemActionRequest = Schemas["SystemActionRequest"];
export type SystemAction = SystemActionRequest["action"];
export type SystemSettings = Schemas["SystemSettings"];
export type Task = Schemas["TaskModel"];
export type TaskContext = Schemas["TaskContextModel"];
export type TaskCreateInput = Schemas["TaskCreate"];
export type TaskListInput = NonNullable<
  operations["tasks.list"]["parameters"]["query"]
>;
export type TaskPage = Schemas["TaskPageModel"];
export type Version = Schemas["VersionModel"];
export type VersionAcceptInput = Schemas["VersionAcceptInput"];
export type VersionListInput = NonNullable<
  operations["versions.list"]["parameters"]["query"]
>;
export type VersionPage = Schemas["VersionPageModel"];
export type VoiceAudio = Schemas["VoiceAudioModel"];
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
  health: { params: EmptyParams; result: Health };
  "projects.create": { params: ProjectCreateInput; result: ProjectContext };
  "projects.import": { params: ProjectImportInput; result: ProjectContext };
  "projects.get": { params: { project_id: string }; result: Project };
  "projects.list": { params: ProjectListInput; result: ProjectPage };
  "conversations.create": { params: ConversationCreateInput; result: Conversation };
  "conversations.get": { params: { conversation_id: string }; result: Conversation };
  "conversations.list": {
    params: ConversationListInput;
    result: ConversationPage;
  };
  "documents.import": { params: DocumentImportInput; result: DocumentContext };
  "documents.list": { params: DocumentListInput; result: DocumentPage };
  "documents.get": {
    params: { task_id: string; document_id: string };
    result: DocumentContext;
  };
  "documents.search": { params: DocumentSearchInput; result: DocumentSearchPage };
  "documents.delete": { params: DocumentDeleteInput; result: DocumentContext };
  "tasks.create": { params: TaskCreateInput; result: TaskContext };
  "tasks.get": { params: { task_id: string }; result: Task };
  "tasks.list": { params: TaskListInput; result: TaskPage };
  "tasks.review": { params: { task_id: string }; result: Checkpoint };
  "changesets.propose": { params: ChangesetProposal; result: PendingChangeset };
  "approvals.decide": { params: ApprovalDecisionInput; result: Changeset };
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
  "runtimes.get": { params: { runtime_id: string }; result: Runtime };
  "runtimes.health": { params: { task_id: string }; result: RuntimeHealth };
  "system.actions.execute": {
    params: SystemActionRequest;
    result: SystemActionExecution;
  };
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
  "assistant.turns.retry": {
    params: AssistantTurnRetryInput;
    result: AssistantTurn;
  };
  "messages.list": { params: MessageListInput; result: MessagePage };
  "voice.synthesize": { params: VoiceSynthesizeInput; result: VoiceAudio };
  "voice.transcribe": { params: VoiceTranscribeInput; result: VoiceTranscript };
  "events.subscribe": { params: { cursor: number }; result: EventBatch };
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
}

export type CoreMethodName = keyof CoreMethodMap;

type GeneratedRpcMethod = Exclude<keyof operations, "cloud.ready" | `sync.${string}`>;
type CoreMethodContractCoverage = Exclude<GeneratedRpcMethod, CoreMethodName> extends never
  ? Exclude<CoreMethodName, GeneratedRpcMethod> extends never
    ? true
    : never
  : never;

export const CORE_METHOD_CONTRACT_COMPLETE: CoreMethodContractCoverage = true;
