import type {
  ApprovalDecisionInput,
  ApprovalListInput,
  AssistantTurnCancelInput,
  AssistantTurnCreateInput,
  AssistantTurnRetryInput,
  ChangesetProposal,
  ConversationCreateInput,
  ConversationDeleteInput,
  ConversationListInput,
  ConversationMoveToProjectInput,
  ConversationUpdateInput,
  CoreMethodMap,
  CoreMethodName,
  DocumentDeleteInput,
  DocumentImportInput,
  DocumentListInput,
  DocumentSearchInput,
  EventEnvelope,
  EventSubscriptionOptions,
  ExecutionSettingsUpdateInput,
  MemoryClaimPromoteInput,
  MemoryClaimResolveInput,
  MemoryClaimSupersedeInput,
  MemoryForgetInput,
  MemoryNamespace,
  MemoryObserveInput,
  MemorySearchInput,
  McpServerAcceptInput,
  McpServerConfigureInput,
  McpServerDeleteInput,
  McpServerDiscoverInput,
  McpServerSetEnabledInput,
  MessageListInput,
  ProjectCreateInput,
  ProjectImportInput,
  ProjectListInput,
  PreviewResolveInput,
  PreviewStartInput,
  PreviewStopInput,
  ProviderHealthInput,
  SystemActionRequest,
  TaskCreateInput,
  TaskArchiveInput,
  TaskListInput,
  TaskMetadataUpdateInput,
  VersionAcceptInput,
  VersionListInput,
  VoiceSynthesizeInput,
  VoiceSessionStartInput,
  VoiceTranscribeInput,
  WorkspaceFileReadInput,
  WorkspaceFileStreamInput,
  WorkspaceFileMutateInput,
  WorkspaceExportInput,
  WorkspaceVersionInput,
  FileSetGetInput,
  FileSetResolveInput,
  FilePresentInput,
  FileRenderJobCancelInput,
  RendererPackInstallInput,
  RendererPackRemoveInput,
} from "./contracts";

export const DEFAULT_EVENT_POLL_MS = 25;

export type * from "./contracts";

export interface CoreCallOptions {
  signal?: AbortSignal;
}

export interface OpenRouterConfigurationInput {
  api_key: string;
  model_id: string;
}

export interface OpenRouterConfigurationStatus {
  configured: boolean;
  model_id: string | null;
}

export interface CoreTransport {
  call<M extends CoreMethodName>(
    method: M,
    params: CoreMethodMap[M]["params"],
    options?: CoreCallOptions,
  ): Promise<CoreMethodMap[M]["result"]>;

  subscribeEvents?(
    cursor: number,
    options?: EventSubscriptionOptions,
  ): AsyncIterable<EventEnvelope>;

  providerOpenRouterStatus?(): Promise<OpenRouterConfigurationStatus>;
  providerOpenRouterConfigure?(
    input: OpenRouterConfigurationInput,
  ): Promise<OpenRouterConfigurationStatus>;
  providerOpenRouterDelete?(): Promise<OpenRouterConfigurationStatus>;
  selectProjectFolder?(): Promise<string | null>;
  openSettingsWindow?(): Promise<void>;
}

export class CoreClient {
  readonly desktop = {
    openSettings: () => {
      if (this.transport.openSettingsWindow === undefined) {
        throw new Error("Settings require the Fairy desktop host");
      }
      return this.transport.openSettingsWindow();
    },
  };

  readonly projects = {
    create: (input: ProjectCreateInput) => this.transport.call("projects.create", input),
    import: (input: ProjectImportInput) => this.transport.call("projects.import", input),
    get: (projectId: string) =>
      this.transport.call("projects.get", { project_id: projectId }),
    list: (input: ProjectListInput = {}) => this.transport.call("projects.list", input),
    selectFolder: () => {
      if (this.transport.selectProjectFolder === undefined) {
        throw new Error("Project folder selection requires the Fairy desktop host");
      }
      return this.transport.selectProjectFolder();
    },
  };

  readonly conversations = {
    create: (input: ConversationCreateInput) =>
      this.transport.call("conversations.create", input),
    get: (conversationId: string) =>
      this.transport.call("conversations.get", { conversation_id: conversationId }),
    list: (input: ConversationListInput = {}) =>
      this.transport.call("conversations.list", input),
    update: (input: ConversationUpdateInput) =>
      this.transport.call("conversations.update", input),
    delete: (input: ConversationDeleteInput) =>
      this.transport.call("conversations.delete", input),
    moveToProject: (input: ConversationMoveToProjectInput) =>
      this.transport.call("conversations.move_to_project", input),
  };

  readonly tasks = {
    archive: (input: TaskArchiveInput) => this.transport.call("tasks.archive", input),
    create: (input: TaskCreateInput) => this.transport.call("tasks.create", input),
    get: (taskId: string) => this.transport.call("tasks.get", { task_id: taskId }),
    list: (input: TaskListInput = {}) => this.transport.call("tasks.list", input),
    review: (taskId: string) => this.transport.call("tasks.review", { task_id: taskId }),
    updateMetadata: (input: TaskMetadataUpdateInput) =>
      this.transport.call("tasks.update_metadata", input),
  };

  readonly changesets = {
    propose: (input: ChangesetProposal) => this.transport.call("changesets.propose", input),
  };

  readonly approvals = {
    decide: (input: ApprovalDecisionInput) => this.transport.call("approvals.decide", input),
    list: (input: ApprovalListInput = {}) =>
      this.transport.call("approvals.list", input),
  };

  readonly versions = {
    get: (versionId: string) =>
      this.transport.call("versions.get", { version_id: versionId }),
    list: (input: VersionListInput = {}) =>
      this.transport.call("versions.list", input),
    accept: (input: VersionAcceptInput) => this.transport.call("versions.accept", input),
    discard: (taskId: string) =>
      this.transport.call("versions.discard", { task_id: taskId }),
  };

  readonly workspaces = {
    get: (workspaceId: string) =>
      this.transport.call("workspaces.get", { workspace_id: workspaceId }),
    listFiles: (input: WorkspaceVersionInput) =>
      this.transport.call("workspaces.files.list", input),
    readFile: (input: WorkspaceFileReadInput) =>
      this.transport.call("workspaces.files.read", input),
    openStream: (input: WorkspaceFileStreamInput) =>
      this.transport.call("files.open_stream", input),
    mutateFiles: (input: WorkspaceFileMutateInput) =>
      this.transport.call("workspaces.files.mutate", input),
    export: (input: WorkspaceExportInput) =>
      this.transport.call("workspaces.export", input),
  };

  readonly files = {
    probe: (input: WorkspaceFileReadInput) => this.transport.call("files.probe", input),
    present: (input: FilePresentInput) => this.transport.call("files.present", input),
    cancel: (input: FileRenderJobCancelInput) =>
      this.transport.call("files.cancel", input),
    openStream: (input: WorkspaceFileStreamInput) =>
      this.transport.call("files.open_stream", input),
  };

  readonly rendererPacks = {
    list: () => this.transport.call("renderer_packs.list", {}),
    health: () => this.transport.call("renderer_packs.health", {}),
    install: (input: RendererPackInstallInput) =>
      this.transport.call("renderer_packs.install", input),
    update: (input: RendererPackInstallInput) =>
      this.transport.call("renderer_packs.update", input),
    remove: (input: RendererPackRemoveInput) =>
      this.transport.call("renderer_packs.remove", input),
  };

  readonly fileSets = {
    get: (input: FileSetGetInput) => this.transport.call("file_sets.get", input),
    resolve: (input: FileSetResolveInput) =>
      this.transport.call("file_sets.resolve", input),
  };

  readonly capabilities = {
    get: () => this.transport.call("capabilities.get", {}),
  };

  readonly permissions = {
    get: () => this.transport.call("permissions.get", {}),
    update: (input: ExecutionSettingsUpdateInput) =>
      this.transport.call("permissions.update", input),
  };

  readonly providers = {
    list: () => this.transport.call("providers.list", {}),
    health: (profileId?: string) =>
      this.transport.call(
        "providers.health",
        profileId === undefined
          ? {}
          : ({ profile_id: profileId } satisfies ProviderHealthInput),
      ),
    openRouterStatus: () => this.requireProviderHost("status")(),
    configureOpenRouter: (input: OpenRouterConfigurationInput) =>
      this.requireProviderHost("configure")(input),
    deleteOpenRouter: () => this.requireProviderHost("delete")(),
  };

  readonly skills = {
    list: () => this.transport.call("skills.list", {}),
  };

  readonly mcp = {
    servers: {
      list: () => this.transport.call("mcp.servers.list", {}),
      configure: (input: McpServerConfigureInput) =>
        this.transport.call("mcp.servers.configure", input),
      discover: (input: McpServerDiscoverInput) =>
        this.transport.call("mcp.servers.discover", input),
      accept: (input: McpServerAcceptInput) =>
        this.transport.call("mcp.servers.accept", input),
      setEnabled: (input: McpServerSetEnabledInput) =>
        this.transport.call("mcp.servers.set_enabled", input),
      delete: (input: McpServerDeleteInput) =>
        this.transport.call("mcp.servers.delete", input),
    },
  };

  readonly runtimes = {
    get: (runtimeId: string) =>
      this.transport.call("runtimes.get", { runtime_id: runtimeId }),
    health: (taskId: string) =>
      this.transport.call("runtimes.health", { task_id: taskId }),
  };

  readonly systemActions = {
    execute: (input: SystemActionRequest) =>
      this.transport.call("system.actions.execute", input),
  };

  readonly previews = {
    start: (input: PreviewStartInput) => this.transport.call("previews.start", input),
    get: (previewId: string) =>
      this.transport.call("previews.get", { preview_id: previewId }),
    resolve: (input: PreviewResolveInput) =>
      this.transport.call("previews.resolve", input),
    stop: (input: PreviewStopInput) => this.transport.call("previews.stop", input),
  };

  readonly artifacts = {
    list: (taskId: string) =>
      this.transport.call("artifacts.list", { task_id: taskId }),
    read: (artifactId: string) =>
      this.transport.call("artifacts.read", { artifact_id: artifactId }),
  };

  readonly documents = {
    import: (input: DocumentImportInput) =>
      this.transport.call("documents.import", input),
    list: (input: DocumentListInput) =>
      this.transport.call("documents.list", input),
    get: (taskId: string, documentId: string) =>
      this.transport.call("documents.get", {
        task_id: taskId,
        document_id: documentId,
      }),
    search: (input: DocumentSearchInput) =>
      this.transport.call("documents.search", input),
    delete: (input: DocumentDeleteInput) =>
      this.transport.call("documents.delete", input),
  };

  readonly assistant = {
    turns: {
      create: (input: AssistantTurnCreateInput) =>
        this.transport.call("assistant.turns.create", input),
      get: (turnId: string) =>
        this.transport.call("assistant.turns.get", { turn_id: turnId }),
      cancel: (input: AssistantTurnCancelInput) =>
        this.transport.call("assistant.turns.cancel", input),
      run: (turnId: string) =>
        this.transport.call("assistant.turns.run", { turn_id: turnId }),
      start: (turnId: string) =>
        this.transport.call("assistant.turns.start", { turn_id: turnId }),
      retry: (input: AssistantTurnRetryInput) =>
        this.transport.call("assistant.turns.retry", input),
    },
  };

  readonly messages = {
    list: (input: MessageListInput) => this.transport.call("messages.list", input),
  };

  readonly voice = {
    transcribe: (input: VoiceTranscribeInput) =>
      this.transport.call("voice.transcribe", input),
    synthesize: (input: VoiceSynthesizeInput, signal?: AbortSignal) =>
      this.transport.call("voice.synthesize", input, { signal }),
    sessions: {
      start: (input: VoiceSessionStartInput) =>
        this.transport.call("voice.sessions.start", input),
      get: (sessionId: string) =>
        this.transport.call("voice.sessions.get", { session_id: sessionId }),
      cancel: (sessionId: string) =>
        this.transport.call("voice.sessions.cancel", { session_id: sessionId }),
    },
  };

  readonly memory = {
    observations: {
      create: (input: MemoryObserveInput) =>
        this.transport.call("memory.observations.create", input),
      list: (taskId: string, namespace: MemoryNamespace) =>
        this.transport.call("memory.observations.list", {
          task_id: taskId,
          namespace,
        }),
    },
    claims: {
      promote: (input: MemoryClaimPromoteInput) =>
        this.transport.call("memory.claims.promote", input),
      get: (taskId: string, claimId: string) =>
        this.transport.call("memory.claims.get", {
          task_id: taskId,
          claim_id: claimId,
        }),
      list: (taskId: string, namespace: MemoryNamespace) =>
        this.transport.call("memory.claims.list", {
          task_id: taskId,
          namespace,
        }),
      supersede: (input: MemoryClaimSupersedeInput) =>
        this.transport.call("memory.claims.supersede", input),
      resolveConflict: (input: MemoryClaimResolveInput) =>
        this.transport.call("memory.claims.resolve_conflict", input),
    },
    search: (input: MemorySearchInput) => this.transport.call("memory.search", input),
    snapshots: {
      get: (taskId: string, snapshotId: string) =>
        this.transport.call("memory.snapshots.get", {
          task_id: taskId,
          snapshot_id: snapshotId,
        }),
    },
    projection: {
      health: (taskId: string) =>
        this.transport.call("memory.projection.health", { task_id: taskId }),
    },
    forget: (input: MemoryForgetInput) => this.transport.call("memory.forget", input),
  };

  readonly events = {
    subscribe: (cursor = 0, options: EventSubscriptionOptions = {}) =>
      this.subscribeToEvents(cursor, options),
  };

  constructor(private readonly transport: CoreTransport) {}

  health() {
    return this.transport.call("health", {});
  }

  private requireProviderHost(operation: "status"): () => Promise<OpenRouterConfigurationStatus>;
  private requireProviderHost(operation: "delete"): () => Promise<OpenRouterConfigurationStatus>;
  private requireProviderHost(
    operation: "configure",
  ): (input: OpenRouterConfigurationInput) => Promise<OpenRouterConfigurationStatus>;
  private requireProviderHost(operation: "status" | "configure" | "delete") {
    const selected =
      operation === "status"
        ? this.transport.providerOpenRouterStatus
        : operation === "configure"
          ? this.transport.providerOpenRouterConfigure
          : this.transport.providerOpenRouterDelete;
    if (selected === undefined) {
      throw new Error("Provider credential management requires the Fairy desktop host");
    }
    return selected.bind(this.transport);
  }

  private subscribeToEvents(
    cursor: number,
    options: EventSubscriptionOptions,
  ): AsyncIterable<EventEnvelope> {
    return (
      this.transport.subscribeEvents?.(cursor, options) ??
      this.pollEvents(cursor, options)
    );
  }

  private async *pollEvents(
    cursor: number,
    options: EventSubscriptionOptions,
  ): AsyncIterable<EventEnvelope> {
    let current = cursor;
    while (!options.signal?.aborted) {
      const batch = await this.transport.call("events.subscribe", { cursor: current });
      for (const event of batch.items) {
        if (event.cursor <= current) continue;
        current = event.cursor;
        yield event;
      }
      if (batch.items.length === 0) {
        await waitForPoll(options.pollIntervalMs ?? DEFAULT_EVENT_POLL_MS, options.signal);
      }
    }
  }
}

function waitForPoll(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const timeout = window.setTimeout(finish, milliseconds);
    signal?.addEventListener("abort", finish, { once: true });

    function finish() {
      window.clearTimeout(timeout);
      signal?.removeEventListener("abort", finish);
      resolve();
    }
  });
}
