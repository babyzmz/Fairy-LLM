import type { AssistantDraft, AssistantTurnClient, OptimisticUserMessage } from "../chat/useAssistantTurn";
import type { TurnTraceQueryState } from "../chat/useTurnTraces";
import type {
  Approval,
  AssistantBackgroundTask,
  AssistantBackgroundTaskPage,
  BrowserActionInput,
  BrowserSession,
  BrowserSnapshot,
  BrowserWorkerHealth,
  AssistantTurn,
  CapabilityManifest,
  Conversation,
  CoreClient,
  DocumentContext,
  DocumentSearchHit,
  EventEnvelope,
  ExecutionSettings,
  Message,
  MemorySearchHit,
  KnowledgeGraph,
  KnowledgeItem,
  ProjectKnowledgeOverview,
  ObsidianConnectorHealth,
  ObsidianSource,
  ObsidianVaultSelection,
  ObsidianVaultItemContent,
  ObsidianVaultItem,
  MediaGenerationJob,
  ModelCatalogPage,
  ModelSelectionPreference,
  PreviewActivation,
  PreviewContext,
  Project,
  ProviderHealth,
  ProviderProfile,
  RealtimeTranscriptEntry,
  RuntimeHealth,
  SettingsCategoryId,
  Task,
  TurnTrace,
  Version,
  WorkspaceFile,
  WorkspaceFileContent,
  FileReadSession,
  FilePresentationResult,
  FileSet,
  AnnotationDocument,
  AssetSet,
  AnnotationResult,
  SelectionReference,
  WorkspaceExport,
  FileCompareResult,
} from "../core/client";
import type { PendingImageAttachment } from "../perception/CaptureControl";

export type WorkspaceMode = "project" | "chat";
export type PermissionProfile = "observe" | "standard" | "autonomous";
export type BackgroundTaskAction = "pause" | "resume" | "cancel" | "run_now";

export interface ObsidianSourceProjection {
  sourceId: string;
  sourceRevision: number | null;
  itemCount: number;
  loading: boolean;
  error: string | null;
}

export interface WorkspaceClient extends AssistantTurnClient {
  assistant: {
    backgroundTasks: CoreClient["assistant"]["backgroundTasks"];
    schedules: CoreClient["assistant"]["schedules"];
    turns: AssistantTurnClient["assistant"]["turns"] &
      Pick<CoreClient["assistant"]["turns"], "trace">;
  };
  desktop: Pick<CoreClient["desktop"], "openSettings">;
  trash?: Pick<CoreClient["trash"], "purgeAll">;
  health: CoreClient["health"];
  projects: Pick<
    CoreClient["projects"],
    "list" | "create" | "import" | "get" | "updateMetadata" | "archive" | "delete" | "selectFolder"
  >;
  conversations: Pick<CoreClient["conversations"], "list" | "create" | "get" | "update" | "delete" | "moveToProject">;
  tasks: Pick<CoreClient["tasks"], "list" | "create" | "get" | "review" | "updateMetadata" | "archive">;
  approvals: Pick<CoreClient["approvals"], "list" | "decide">;
  versions: Pick<CoreClient["versions"], "list" | "accept" | "discard">;
  workspaces: Pick<
    CoreClient["workspaces"],
    "get" | "listFiles" | "readFile" | "openStream" | "mutateFiles" | "export"
  >;
  files: Pick<CoreClient["files"], "present" | "compare">;
  fileSets: Pick<CoreClient["fileSets"], "resolve">;
  assetSets: Pick<CoreClient["assetSets"], "list">;
  annotations: Pick<CoreClient["annotations"], "list" | "update">;
  selections: Pick<CoreClient["selections"], "create">;
  runtimes: Pick<CoreClient["runtimes"], "health">;
  previews: Pick<CoreClient["previews"], "activate" | "resolve" | "start" | "stop">;
  browser: CoreClient["browser"];
  capabilities: Pick<CoreClient["capabilities"], "get">;
  permissions: Pick<CoreClient["permissions"], "get" | "update">;
  providers: Pick<
    CoreClient["providers"],
    "list" | "health" | "openRouterStatus" | "configureOpenRouter" | "deleteOpenRouter"
  >;
  models: Pick<CoreClient["models"], "catalog" | "selection">;
  media: {
    jobs: Pick<CoreClient["media"]["jobs"], "list">;
    videos: Pick<CoreClient["media"]["videos"], "cancel">;
  };
  skills: Pick<CoreClient["skills"], "list">;
  mcp: {
    servers: Pick<CoreClient["mcp"]["servers"], "list" | "configure" | "discover" | "accept" | "setEnabled" | "delete">;
  };
  messages: Pick<CoreClient["messages"], "list">;
  documents: Pick<CoreClient["documents"], "import" | "list" | "search" | "delete">;
  memory: Pick<CoreClient["memory"], "search" | "forget">;
  knowledge: Pick<CoreClient["knowledge"], "overview" | "listItems" | "graph">;
  obsidian: Pick<
    CoreClient["obsidian"],
    "selectVault" | "health" | "createSource" | "listSources" | "listItems" | "readItem" | "sync"
  >;
  voice: Pick<CoreClient["voice"], "transcribe" | "synthesize">;
  realtime: { transcript: Pick<CoreClient["realtime"]["transcript"], "list"> };
  systemActions: Pick<CoreClient["systemActions"], "execute">;
  events: Pick<CoreClient["events"], "sourceId" | "state" | "list" | "subscribe">;
}

export interface WorkspaceModel {
  state: "loading" | "offline" | "empty" | "ready";
  connectionState: "starting" | "ready" | "offline";
  historyLoading: boolean;
  conversationContentState: "idle" | "loading" | "ready" | "error";
  projectContentState: "idle" | "loading" | "ready" | "error";
  mode: WorkspaceMode;
  statusLabel: string;
  errorMessage: string | null;
  actionError: string | null;
  actionErrorCode: string | null;
  isActing: boolean;
  permissionProfile: PermissionProfile | null;
  permissionSettings: ExecutionSettings | null;
  developerMode: boolean;
  projects: Project[];
  conversations: Conversation[];
  projectConversations: Conversation[];
  chatConversations: Conversation[];
  tasks: Task[];
  allTasks: Task[];
  versions: Version[];
  approvals: Approval[];
  chatApprovals: Approval[];
  events: EventEnvelope[];
  chatEvents: EventEnvelope[];
  presenceEvents: EventEnvelope[];
  messages: Message[];
  realtimeTranscript: RealtimeTranscriptEntry[];
  providers: ProviderProfile[];
  providerHealth: ProviderHealth[];
  selectedProfileId: string | null;
  modelCatalog: ModelCatalogPage | null;
  modelSelection: ModelSelectionPreference | null;
  modelSelectionLoading: boolean;
  modelSelectionRefreshing: boolean;
  modelSelectionBlockReason: string | null;
  visionAvailable: boolean;
  selectedProject: Project | null;
  selectedConversation: Conversation | null;
  selectedChatConversation: Conversation | null;
  selectedTask: Task | null;
  workspaceTask: Task | null;
  workspaceActivePreviewId: string | null;
  selectedVersion: Version | null;
  preview: PreviewContext | null;
  previewActivation: PreviewActivation | null;
  previewActivationLoading: boolean;
  previewActivationError: string | null;
  runtimeHealth: RuntimeHealth | null;
  browserHealth: BrowserWorkerHealth | null;
  browserSession: BrowserSession | null;
  browserSnapshot: BrowserSnapshot | null;
  browserLoading: boolean;
  browserError: string | null;
  workspaceFiles: WorkspaceFile[];
  workspaceGeneration: number;
  knowledgeOverview: ProjectKnowledgeOverview | null;
  knowledgeItems: KnowledgeItem[];
  knowledgeGraph: KnowledgeGraph | null;
  knowledgeLoading: boolean;
  knowledgeError: string | null;
  obsidianHealth: ObsidianConnectorHealth | null;
  obsidianSources: ObsidianSource[];
  obsidianItems: ObsidianVaultItem[];
  obsidianSourceProjections: ObsidianSourceProjection[];
  obsidianLoading: boolean;
  obsidianError: string | null;
  mediaJobs: MediaGenerationJob[];
  assetSets: AssetSet[];
  workspaceFilesLoading: boolean;
  mediaJobsLoading: boolean;
  capabilities: CapabilityManifest | null;
  chatTurn: AssistantTurn | null;
  turnTraces: Record<string, TurnTrace>;
  turnTraceStates: Record<string, TurnTraceQueryState>;
  chatStreamedText: string;
  chatPendingUserMessage: OptimisticUserMessage | null;
  chatBusy: boolean;
  chatError: string | null;
  backgroundTasks: AssistantBackgroundTaskPage;
  backgroundTasksLoading: boolean;
  projectTurn: AssistantTurn | null;
  projectTrace: TurnTrace | null;
  projectTraceState: TurnTraceQueryState | null;
  projectBusy: boolean;
  projectError: string | null;
  petTaskId: string | null;
  setMode(mode: WorkspaceMode): void;
  setPermissionProfile(profile: PermissionProfile): Promise<void>;
  setCapabilityEnabled(name: string, enabled: boolean): Promise<void>;
  listDocuments(): Promise<DocumentContext[]>;
  searchDocuments(query: string): Promise<DocumentSearchHit[]>;
  deleteDocument(documentId: string): Promise<void>;
  searchMemory(query: string): Promise<MemorySearchHit[]>;
  forgetMemory(targetKind: "observation" | "claim", targetId: string): Promise<void>;
  setDeveloperMode(enabled: boolean): void;
  selectModel(mode: "auto" | "manual", modelId: string | null): Promise<void>;
  refreshModelCatalog(): Promise<void>;
  selectProject(projectId: string): void;
  selectConversation(conversationId: string): void;
  selectChatConversation(conversationId: string): void;
  prefetchConversation(conversation: Conversation): Promise<void>;
  selectTask(taskId: string): void;
  createProject(name: string): Promise<void>;
  importProject(name: string, sourcePath: string): Promise<void>;
  selectProjectFolder(): Promise<string | null>;
  selectObsidianVault(): Promise<ObsidianVaultSelection | null>;
  connectObsidianVault(
    selection: ObsidianVaultSelection,
    options: {
      readScope: "selected_directories" | "whole_vault";
      allowedDirectories: string[];
      wholeVaultConfirmed: boolean;
      managedDirectory: string;
    },
  ): Promise<void>;
  syncObsidianSource(source: ObsidianSource): Promise<void>;
  readObsidianItem(item: ObsidianVaultItem, signal?: AbortSignal): Promise<ObsidianVaultItemContent>;
  createChatConversation(): Promise<void>;
  createPetChatConversation(): Promise<void>;
  createProjectConversation(project: Project): Promise<void>;
  renameProject(project: Project, name: string): Promise<void>;
  setProjectPinned(project: Project, pinned: boolean): Promise<void>;
  archiveProject(project: Project): Promise<void>;
  deleteProject(project: Project, cancelActive?: boolean): Promise<void>;
  renameConversation(conversation: Conversation, title: string): Promise<void>;
  setConversationPinned(conversation: Conversation, pinned: boolean): Promise<void>;
  deleteConversation(conversation: Conversation): Promise<void>;
  moveConversationToProject(conversation: Conversation, project: Project): Promise<void>;
  renameTask(task: Task, title: string): Promise<void>;
  setTaskPinned(task: Task, pinned: boolean): Promise<void>;
  archiveTask(task: Task): Promise<void>;
  createTask(userRequest: string): Promise<void>;
  sendChatMessage(value: string, files: File[], images?: PendingImageAttachment[]): Promise<void>;
  sendProjectMessage(value: string, files: File[], images?: PendingImageAttachment[]): Promise<void>;
  sendPetMessage(value: string): Promise<void>;
  cancelPetTurn(): Promise<void>;
  cancelChatTurn(): Promise<void>;
  pauseChatTurn(): Promise<void>;
  resumeChatTurn(): Promise<void>;
  steerChatTurn(instruction: string): Promise<void>;
  retryChatTurn(): Promise<void>;
  manageBackgroundTask(
    task: AssistantBackgroundTask,
    action: BackgroundTaskAction,
  ): Promise<void>;
  openBackgroundTask(task: AssistantBackgroundTask): void;
  retryPendingChatMessage(): Promise<void>;
  deletePendingChatMessage(): void;
  takePendingChatMessageForEdit(): AssistantDraft | null;
  copyMessage(taskId: string, content: string): Promise<void>;
  openMessageLink(taskId: string, url: string): Promise<void>;
  readWorkspaceFile(path: string): Promise<WorkspaceFileContent>;
  readWorkspaceSource(workspaceId: string, versionId: string, path: string): Promise<WorkspaceFileContent>;
  openWorkspaceFileStream(path: string): Promise<FileReadSession>;
  presentWorkspaceFile(path: string): Promise<FilePresentationResult>;
  compareWorkspaceFile(leftVersionId: string, rightVersionId: string, path: string): Promise<FileCompareResult>;
  resolveWorkspaceFileSet(path: string): Promise<FileSet>;
  listFileAnnotations(presentation: FilePresentationResult): Promise<AnnotationResult>;
  updateFileAnnotations(
    presentation: FilePresentationResult,
    current: AnnotationDocument | null,
    annotations: Array<Record<string, unknown>>,
  ): Promise<AnnotationDocument>;
  createTextSelection(presentation: FilePresentationResult, start: number, end: number): Promise<SelectionReference>;
  createSceneSelection(presentation: FilePresentationResult, nodePath: string): Promise<SelectionReference>;
  revealWorkspaceFile(path: string): Promise<void>;
  refreshWorkspaceFiles(): Promise<void>;
  uploadWorkspaceFiles(files: Array<{ path: string; contentBase64: string }>): Promise<void>;
  renameWorkspaceFile(file: WorkspaceFile, destinationPath: string): Promise<void>;
  deleteWorkspaceFile(file: WorkspaceFile): Promise<void>;
  exportWorkspace(): Promise<WorkspaceExport>;
  cancelMediaJob(job: MediaGenerationJob): Promise<void>;
  cancelProjectTurn(): Promise<void>;
  pauseProjectTurn(): Promise<void>;
  resumeProjectTurn(): Promise<void>;
  steerProjectTurn(instruction: string): Promise<void>;
  decideApproval(approvalId: string, approved: boolean): Promise<void>;
  startPreview(): Promise<void>;
  stopPreview(): Promise<void>;
  startBrowser(initialUrl?: string): Promise<void>;
  stopBrowser(): Promise<void>;
  navigateBrowser(url: string): Promise<void>;
  openBrowserTab(url?: string): Promise<void>;
  selectBrowserTab(tabId: string): Promise<void>;
  closeBrowserTab(tabId: string): Promise<void>;
  executeBrowserAction(input: Omit<BrowserActionInput, "session_id" | "tab_id" | "idempotency_key">): Promise<void>;
  refreshBrowser(): Promise<void>;
  setBrowserSurfaceActive(active: boolean): void;
  reviewTask(): Promise<void>;
  acceptVersion(): Promise<void>;
  discardVersion(): Promise<void>;
  retryWorkspace(): Promise<void>;
  openSettings(category?: SettingsCategoryId): Promise<void>;
}
