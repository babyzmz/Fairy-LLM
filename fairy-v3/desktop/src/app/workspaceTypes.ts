import type {
  AssistantDraft,
  AssistantTurnClient,
  OptimisticUserMessage,
} from "../chat/useAssistantTurn";
import type {
  Approval,
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
  McpServer,
  McpToolPolicyInput,
  OpenRouterConfigurationStatus,
  PreviewContext,
  Project,
  ProviderHealth,
  ProviderProfile,
  RuntimeHealth,
  Skill,
  Task,
  Version,
  WorkspaceFile,
  WorkspaceFileContent,
  WorkspaceExport,
} from "../core/client";
import type { PendingImageAttachment } from "../perception/CaptureControl";
import type { McpServerDraft } from "../settings/extensionTypes";

export type WorkspaceMode = "project" | "chat";
export type PermissionProfile = "observe" | "standard" | "autonomous";

export interface WorkspaceClient extends AssistantTurnClient {
  desktop: Pick<CoreClient["desktop"], "openSettings">;
  health: CoreClient["health"];
  projects: Pick<
    CoreClient["projects"],
    "list" | "create" | "import" | "selectFolder"
  >;
  conversations: Pick<
    CoreClient["conversations"],
    "list" | "create" | "update" | "delete" | "moveToProject"
  >;
  tasks: Pick<
    CoreClient["tasks"],
    "list" | "create" | "review" | "updateMetadata" | "archive"
  >;
  approvals: Pick<CoreClient["approvals"], "list" | "decide">;
  versions: Pick<CoreClient["versions"], "list" | "accept" | "discard">;
  workspaces: Pick<
    CoreClient["workspaces"],
    "get" | "listFiles" | "readFile" | "mutateFiles" | "export"
  >;
  runtimes: Pick<CoreClient["runtimes"], "health">;
  previews: Pick<CoreClient["previews"], "resolve" | "start" | "stop">;
  capabilities: Pick<CoreClient["capabilities"], "get">;
  permissions: Pick<CoreClient["permissions"], "get" | "update">;
  providers: Pick<
    CoreClient["providers"],
    | "list"
    | "health"
    | "openRouterStatus"
    | "configureOpenRouter"
    | "deleteOpenRouter"
  >;
  skills: Pick<CoreClient["skills"], "list">;
  mcp: {
    servers: Pick<
      CoreClient["mcp"]["servers"],
      "list" | "configure" | "discover" | "accept" | "setEnabled" | "delete"
    >;
  };
  messages: Pick<CoreClient["messages"], "list">;
  documents: Pick<
    CoreClient["documents"],
    "import" | "list" | "search" | "delete"
  >;
  memory: Pick<CoreClient["memory"], "search" | "forget">;
  voice: Pick<CoreClient["voice"], "transcribe" | "synthesize">;
  systemActions: Pick<CoreClient["systemActions"], "execute">;
  events: Pick<CoreClient["events"], "subscribe">;
}

export interface WorkspaceModel {
  state: "loading" | "offline" | "empty" | "ready";
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
  providers: ProviderProfile[];
  providerHealth: ProviderHealth[];
  openRouterStatus: OpenRouterConfigurationStatus | null;
  skills: Skill[];
  mcpServers: McpServer[];
  selectedProfileId: string | null;
  selectedProject: Project | null;
  selectedConversation: Conversation | null;
  selectedChatConversation: Conversation | null;
  selectedTask: Task | null;
  workspaceTask: Task | null;
  selectedVersion: Version | null;
  preview: PreviewContext | null;
  runtimeHealth: RuntimeHealth | null;
  workspaceFiles: WorkspaceFile[];
  workspaceFilesLoading: boolean;
  capabilities: CapabilityManifest | null;
  chatTurn: AssistantTurn | null;
  chatStreamedText: string;
  chatPendingUserMessage: OptimisticUserMessage | null;
  chatBusy: boolean;
  chatError: string | null;
  projectTurn: AssistantTurn | null;
  projectBusy: boolean;
  projectError: string | null;
  petTaskId: string | null;
  setMode(mode: WorkspaceMode): void;
  setPermissionProfile(profile: PermissionProfile): Promise<void>;
  setCapabilityEnabled(name: string, enabled: boolean): Promise<void>;
  configureMcpServer(input: McpServerDraft): Promise<void>;
  discoverMcpServer(serverId: string): Promise<void>;
  acceptMcpServer(serverId: string, tools: McpToolPolicyInput[]): Promise<void>;
  setMcpServerEnabled(serverId: string, enabled: boolean): Promise<void>;
  deleteMcpServer(serverId: string): Promise<void>;
  listDocuments(): Promise<DocumentContext[]>;
  searchDocuments(query: string): Promise<DocumentSearchHit[]>;
  deleteDocument(documentId: string): Promise<void>;
  searchMemory(query: string): Promise<MemorySearchHit[]>;
  forgetMemory(
    targetKind: "observation" | "claim",
    targetId: string,
  ): Promise<void>;
  setDeveloperMode(enabled: boolean): void;
  selectProfile(profileId: string): void;
  configureOpenRouter(apiKey: string, modelId: string): Promise<void>;
  deleteOpenRouter(): Promise<void>;
  selectProject(projectId: string): void;
  selectConversation(conversationId: string): void;
  selectChatConversation(conversationId: string): void;
  selectTask(taskId: string): void;
  createProject(name: string): Promise<void>;
  importProject(name: string, sourcePath: string): Promise<void>;
  selectProjectFolder(): Promise<string | null>;
  createChatConversation(): Promise<void>;
  createPetChatConversation(): Promise<void>;
  renameConversation(conversation: Conversation, title: string): Promise<void>;
  setConversationPinned(
    conversation: Conversation,
    pinned: boolean,
  ): Promise<void>;
  deleteConversation(conversation: Conversation): Promise<void>;
  moveConversationToProject(
    conversation: Conversation,
    project: Project,
  ): Promise<void>;
  renameTask(task: Task, title: string): Promise<void>;
  setTaskPinned(task: Task, pinned: boolean): Promise<void>;
  archiveTask(task: Task): Promise<void>;
  createTask(userRequest: string): Promise<void>;
  sendChatMessage(
    value: string,
    files: File[],
    images?: PendingImageAttachment[],
  ): Promise<void>;
  sendProjectMessage(
    value: string,
    files: File[],
    images?: PendingImageAttachment[],
  ): Promise<void>;
  sendPetMessage(value: string): Promise<void>;
  cancelPetTurn(): Promise<void>;
  cancelChatTurn(): Promise<void>;
  retryChatTurn(): Promise<void>;
  retryPendingChatMessage(): Promise<void>;
  deletePendingChatMessage(): void;
  takePendingChatMessageForEdit(): AssistantDraft | null;
  copyMessage(taskId: string, content: string): Promise<void>;
  openMessageLink(taskId: string, url: string): Promise<void>;
  readWorkspaceFile(path: string): Promise<WorkspaceFileContent>;
  revealWorkspaceFile(path: string): Promise<void>;
  refreshWorkspaceFiles(): Promise<void>;
  uploadWorkspaceFiles(files: Array<{ path: string; contentBase64: string }>): Promise<void>;
  renameWorkspaceFile(file: WorkspaceFile, destinationPath: string): Promise<void>;
  deleteWorkspaceFile(file: WorkspaceFile): Promise<void>;
  exportWorkspace(): Promise<WorkspaceExport>;
  cancelProjectTurn(): Promise<void>;
  decideApproval(approvalId: string, approved: boolean): Promise<void>;
  startPreview(): Promise<void>;
  stopPreview(): Promise<void>;
  reviewTask(): Promise<void>;
  acceptVersion(): Promise<void>;
  discardVersion(): Promise<void>;
  retryWorkspace(): Promise<void>;
  openSettings(): Promise<void>;
}
