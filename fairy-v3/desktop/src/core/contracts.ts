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
export type CapabilityManifest = Schemas["CapabilityManifestModel"];
export type CapabilityRequest = Schemas["CapabilityRequest"];
export type Changeset = Schemas["ChangesetModel"];
export type ChangesetProposal = Schemas["ChangesetProposal"];
export type Checkpoint = Schemas["CheckpointModel"];
export type Conversation = Schemas["ConversationModel"];
export type ConversationCreateInput = Schemas["ConversationCreate"];
export type ConversationListInput = NonNullable<
  operations["conversations.list"]["parameters"]["query"]
>;
export type ConversationPage = Schemas["ConversationPageModel"];
export type EventEnvelope = Schemas["EventEnvelopeModel"];
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
export type Runtime = Schemas["RuntimeModel"];
export type RuntimeHealth = Schemas["RuntimeHealthModel"];
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
  "capabilities.get": { params: CapabilityRequest; result: CapabilityManifest };
  "runtimes.get": { params: { runtime_id: string }; result: Runtime };
  "runtimes.health": { params: { task_id: string }; result: RuntimeHealth };
  "previews.start": { params: PreviewStartInput; result: PreviewContext };
  "previews.get": { params: { preview_id: string }; result: PreviewContext };
  "previews.resolve": { params: PreviewResolveInput; result: PreviewResolution };
  "previews.stop": { params: PreviewStopInput; result: Preview };
  "artifacts.list": { params: { task_id: string }; result: ArtifactPage };
  "artifacts.read": { params: { artifact_id: string }; result: Artifact };
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
