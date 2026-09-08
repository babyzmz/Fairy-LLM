import type {
  AmbientDialogueEvaluateInput,
  ApprovalDecisionInput,
  BrowserActionInput,
  BrowserSessionStartInput,
  CompanionDigestCreateInput,
  CompanionDigestListInput,
  AssetSetCreateInput,
  ApprovalListInput,
  AssistantScheduleCreateInput,
  AssistantScheduleUpdateInput,
  AssistantMessageSubmitInput,
  AssistantMessageCancelInput,
  AssistantCommandInput,
  AssistantTurnCancelInput,
  AssistantTurnCreateInput,
  AssistantTurnInterpretationInput,
  AssistantTurnRespondInput,
  AssistantTurnRetryInput,
  AssistantTurnSteerInput,
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
  EventStreamState,
  EventSubscriptionOptions,
  ExecutionSettingsUpdateInput,
  MemoryClaimPromoteInput,
  MemoryClaimResolveInput,
  MemoryClaimSupersedeInput,
  MemoryForgetInput,
  MemoryNamespace,
  MemoryObserveInput,
  MemoryProposalActionInput,
  MemorySearchInput,
  MemorySettingsUpdateInput,
  MediaAudioGenerateInput,
  MediaImageGenerateInput,
  MediaVideoCancelInput,
  MediaVideoStartInput,
  GameMemorySaveInput,
  KnowledgeItemListInput,
  KnowledgeSyncStartInput,
  ObsidianSourceCreateInput,
  ObsidianSourceSyncInput,
  ObsidianVaultItemReadInput,
  ModelSelectionUpdateInput,
  McpServerAcceptInput,
  McpServerConfigureInput,
  McpServerDeleteInput,
  McpServerDiscoverInput,
  McpServerSetEnabledInput,
  McpPresetInstallInput,
  MessageListInput,
  ProjectCreateInput,
  ProjectArchiveInput,
  ProjectArchivedListInput,
  ProjectDeleteInput,
  ProjectImportInput,
  ProjectListInput,
  ProjectMetadataUpdateInput,
  PreviewActivateInput,
  PreviewResolveInput,
  PreviewStartInput,
  PreviewStopInput,
  ProviderHealthInput,
  RealtimeSessionReportInput,
  RealtimeSessionStartInput,
  RealtimeSessionStopInput,
  RealtimeAssistanceCancelInput,
  RealtimeAssistanceGetInput,
  RealtimeMemoryProposalActionInput,
  RealtimeMemoryProposalListInput,
  RealtimePersonaSnapshotInput,
  RealtimeTranscriptAppendInput,
  RealtimeTranscriptListInput,
  SystemActionRequest,
  SkillInstallInput,
  SkillCreateInput,
  SkillImportInspectInput,
  SkillImportInstallInput,
  SkillRemoveInput,
  SkillSetEnabledInput,
  SkillUpdateInput,
  TaskCreateInput,
  TaskArchiveInput,
  TaskListInput,
  TaskMetadataUpdateInput,
  TrashItemActionInput,
  TrashListInput,
  TrashPurgeAllInput,
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
  FileCompareInput,
  FileRenderJobCancelInput,
  RendererPackInstallInput,
  RendererPackRemoveInput,
  AnnotationListInput,
  AnnotationUpdateInput,
  SelectionCreateInput,
  EditRecipeCreateInput,
  EditRecipeApplyInput,
  EditRecipeUpdateInput,
} from "./contracts";

export const DEFAULT_EVENT_POLL_MS = 250;
const MAX_IDLE_EVENT_POLL_MS = 5_000;

export type * from "./contracts";

export interface CoreCallOptions {
  signal?: AbortSignal;
}

export interface OpenRouterConfigurationInput {
  api_key: string;
}

export interface OpenRouterConfigurationStatus {
  configured: boolean;
  account_id: string | null;
}

export type RealtimeCredentialProvider = "gemini" | "zhipu";

export interface RealtimeProviderCredentialInput {
  provider: RealtimeCredentialProvider;
  api_key: string;
}

export interface RealtimeProviderCredentialStatus {
  provider: RealtimeCredentialProvider;
  configured: boolean;
  /** Last four characters of the stored key, for a masked preview. */
  hint?: string | null;
}

export type RealtimeWorkerProvider = "gemini_live" | "glm_realtime_flash" | "glm_realtime_air";
export type RealtimeCaptureMode = "selected_window" | "follow_foreground";
export type RealtimeMediaChannel =
  | "microphone"
  | "selected_window"
  | "selected_application_audio"
  | "fairy_render_reference"
  | "voice_output";
export type RealtimeRetryableMediaChannel = Exclude<RealtimeMediaChannel, "voice_output">;

export interface RealtimeWorkerStartInput {
  session_id: string;
  resolution_token: string;
  locale: string;
  backend: "local_mini_cpm_o45" | "cloud_live";
  cloud_provider: RealtimeWorkerProvider | null;
  activity_profile: "auto" | "game" | "focus";
  interaction_intensity: "quiet" | "standard" | "active";
  voice_output: "fairy_voice" | "provider_native_voice" | "text_only";
  source_id: number | null;
  microphone_enabled: boolean;
  screen_enabled: boolean;
  application_audio_enabled: boolean;
  online_assistance_enabled: boolean;
  cloud_microphone_upload_consent: boolean;
  cloud_screen_upload_consent: boolean;
  capture_mode: RealtimeCaptureMode;
  excluded_applications: string[];
}

export interface RealtimeBackendResolutionInput {
  activity_profile: "auto" | "game" | "focus";
  voice_output: "fairy_voice" | "provider_native_voice" | "text_only";
  cloud_microphone_upload_consent: boolean;
  cloud_screen_upload_consent: boolean;
}

export interface RealtimeBackendResolution {
  schema_version: 1;
  resolution_token: string;
  available: boolean;
  backend: "local_mini_cpm_o45" | "cloud_live" | null;
  cloud_provider: RealtimeWorkerProvider | null;
  reason: string | null;
  requires_cloud_upload_consent: boolean;
  preference_revision: number;
}

export interface RealtimeWorkerStatus {
  running: boolean;
  session_id: string | null;
  segment_id: string | null;
  context_epoch: number | null;
  backend: "local_mini_cpm_o45" | "cloud_live" | null;
  cloud_provider: RealtimeWorkerProvider | null;
  action_required: boolean;
  presence_projection: import("../realtime/realtimePresence").RealtimePresenceProjection | null;
  assistance: import("../realtime/realtimePresence").RealtimeAssistanceProjection[];
  resource: {
    policy: {
      level: "normal" | "pressure" | "high" | "critical" | "device_removed";
      video_interval_ms: number;
      background_analysis_allowed: boolean;
      user_initiated_only: boolean;
      media_paused: boolean;
    };
    recovery_samples: number;
    sample_count: number;
  } | null;
  sidecar: {
    restart_used: boolean;
    quarantined: boolean;
    context_interrupted: boolean;
    failure_count: number;
    error_code: string | null;
  };
  capture_scope: {
    mode: RealtimeCaptureMode;
    source_sequence: number;
    source_available: boolean;
    privacy_paused: boolean;
    sensitive_category:
      | "secure_desktop"
      | "fairy_owned"
      | "credential_application"
      | "financial_or_private"
      | "protected_content"
      | "user_excluded"
      | null;
    error_code: string | null;
  } | null;
  media_channels: Array<{
    channel: RealtimeMediaChannel;
    sequence: number;
    status: "starting" | "active" | "paused" | "unavailable" | "recovering";
    error_code: string | null;
  }>;
  audio_input_ms: number;
  audio_output_ms: number;
  video_frame_count: number;
  interruption_count: number;
  tool_call_count: number;
}

export interface RealtimeWorkerToolResultInput {
  session_id: string;
  call_id: string;
  public_summary: string;
  succeeded: boolean;
}

export interface RealtimeWorkerSetInputInput {
  session_id: string;
  microphone: boolean;
  video: boolean;
}

export interface RealtimeWorkerSetPolicyInput {
  session_id: string;
  activity_profile: RealtimeWorkerStartInput["activity_profile"];
  interaction_intensity: RealtimeWorkerStartInput["interaction_intensity"];
}

export interface RealtimeWorkerRetryMediaInput {
  session_id: string;
  channel: RealtimeRetryableMediaChannel;
}

export interface RealtimeWorkerReplaceSourceInput {
  session_id: string;
  source_id: number;
}

export interface RealtimeWorkerWakeInput {
  session_id: string;
}

export interface RealtimeWorkerExtendInput {
  session_id: string;
  additional_minutes: number;
}

export interface RealtimeWorkerSpeechStateInput {
  session_id: string;
  segment_id: string;
  context_epoch: number;
  speech_generation: number;
  speaking: boolean;
}

export interface ObsidianVaultSelection {
  local_path_token: string;
  display_name: string;
  available_directories: string[];
}

export interface CoreTransport {
  readonly eventSourceId?: string;

  call<M extends CoreMethodName>(
    method: M,
    params: CoreMethodMap[M]["params"],
    options?: CoreCallOptions,
  ): Promise<CoreMethodMap[M]["result"]>;

  subscribeEvents?(cursor: number, options?: EventSubscriptionOptions): AsyncIterable<EventEnvelope>;

  providerOpenRouterStatus?(): Promise<OpenRouterConfigurationStatus>;
  providerOpenRouterConfigure?(input: OpenRouterConfigurationInput): Promise<OpenRouterConfigurationStatus>;
  providerOpenRouterDelete?(): Promise<OpenRouterConfigurationStatus>;
  providerRealtimeStatus?(
    provider: RealtimeCredentialProvider,
  ): Promise<RealtimeProviderCredentialStatus>;
  providerRealtimeConfigure?(
    input: RealtimeProviderCredentialInput,
  ): Promise<RealtimeProviderCredentialStatus>;
  providerRealtimeDelete?(
    provider: RealtimeCredentialProvider,
  ): Promise<RealtimeProviderCredentialStatus>;
  realtimeWorkerStatus?(): Promise<RealtimeWorkerStatus>;
  realtimeBackendResolutionPreview?(
    input: RealtimeBackendResolutionInput,
  ): Promise<RealtimeBackendResolution>;
  realtimeWorkerStart?(input: RealtimeWorkerStartInput): Promise<RealtimeWorkerStatus>;
  realtimeWorkerContinue?(input: RealtimeWorkerStartInput): Promise<RealtimeWorkerStatus>;
  realtimeWorkerStop?(sessionId: string): Promise<RealtimeWorkerStatus>;
  realtimeWorkerToolResult?(input: RealtimeWorkerToolResultInput): Promise<void>;
  realtimeWorkerSetInput?(input: RealtimeWorkerSetInputInput): Promise<void>;
  realtimeWorkerSetPolicy?(input: RealtimeWorkerSetPolicyInput): Promise<RealtimeWorkerStatus>;
  realtimeWorkerRetryMedia?(
    input: RealtimeWorkerRetryMediaInput,
  ): Promise<RealtimeWorkerStatus>;
  realtimeWorkerReplaceSource?(
    input: RealtimeWorkerReplaceSourceInput,
  ): Promise<RealtimeWorkerStatus>;
  realtimeWorkerWake?(input: RealtimeWorkerWakeInput): Promise<RealtimeWorkerStatus>;
  realtimeWorkerPausePrivacy?(input: RealtimeWorkerWakeInput): Promise<RealtimeWorkerStatus>;
  realtimeWorkerResumePrivacy?(input: RealtimeWorkerWakeInput): Promise<RealtimeWorkerStatus>;
  realtimeWorkerExtend?(input: RealtimeWorkerExtendInput): Promise<RealtimeWorkerStatus>;
  realtimeWorkerSpeechState?(input: RealtimeWorkerSpeechStateInput): Promise<void>;
  selectProjectFolder?(): Promise<string | null>;
  selectObsidianVault?(): Promise<ObsidianVaultSelection | null>;
  openSettingsWindow?(category?: SettingsCategoryId): Promise<void>;
}

export type SettingsCategoryId =
  | "general"
  | "appearance"
  | "models"
  | "voice"
  | "permissions"
  | "extensions"
  | "knowledge"
  | "pet"
  | "advanced";

export class CoreClient {
  readonly ambient = {
    evaluate: (input: AmbientDialogueEvaluateInput) =>
      this.transport.call("ambient.dialogue.evaluate", input),
  };

  readonly desktop = {
    openSettings: (category?: SettingsCategoryId) => {
      if (this.transport.openSettingsWindow === undefined) {
        throw new Error("Settings require the Fairy desktop host");
      }
      return this.transport.openSettingsWindow(category);
    },
  };

  readonly projects = {
    create: (input: ProjectCreateInput) => this.transport.call("projects.create", input),
    import: (input: ProjectImportInput) => this.transport.call("projects.import", input),
    get: (projectId: string) => this.transport.call("projects.get", { project_id: projectId }),
    list: (input: ProjectListInput = {}) => this.transport.call("projects.list", input),
    updateMetadata: (input: ProjectMetadataUpdateInput) =>
      this.transport.call("projects.update_metadata", input),
    archive: (input: ProjectArchiveInput) => this.transport.call("projects.archive", input),
    delete: (input: ProjectDeleteInput) => this.transport.call("projects.delete", input),
    archived: {
      list: (input: ProjectArchivedListInput = {}) =>
        this.transport.call("projects.archived.list", input),
      restore: (input: ProjectArchiveInput) =>
        this.transport.call("projects.archived.restore", input),
      delete: (input: ProjectDeleteInput) =>
        this.transport.call("projects.archived.delete", input),
    },
    selectFolder: () => {
      if (this.transport.selectProjectFolder === undefined) {
        throw new Error("Project folder selection requires the Fairy desktop host");
      }
      return this.transport.selectProjectFolder();
    },
  };

  readonly browser = {
    health: () => this.transport.call("browser.health", {}),
    profile: () => this.transport.call("browser.profile.get", {}),
    sessions: {
      start: (input: BrowserSessionStartInput) =>
        this.transport.call("browser.sessions.start", input),
      get: (sessionId: string) =>
        this.transport.call("browser.sessions.get", { session_id: sessionId }),
      list: (input: { conversation_id?: string | null; task_id?: string | null; exact_task_scope?: boolean; include_terminal?: boolean } = {}) =>
        this.transport.call("browser.sessions.list", input),
      stop: (sessionId: string) =>
        this.transport.call("browser.sessions.stop", { session_id: sessionId }),
      resume: (sessionId: string) =>
        this.transport.call("browser.sessions.resume", { session_id: sessionId }),
    },
    tabs: {
      open: (sessionId: string, url = "about:blank") =>
        this.transport.call("browser.tabs.open", { session_id: sessionId, url }),
      select: (sessionId: string, tabId: string) =>
        this.transport.call("browser.tabs.select", { session_id: sessionId, tab_id: tabId }),
      close: (sessionId: string, tabId: string) =>
        this.transport.call("browser.tabs.close", { session_id: sessionId, tab_id: tabId }),
    },
    actions: {
      execute: (input: BrowserActionInput) =>
        this.transport.call("browser.actions.execute", input),
    },
    snapshots: {
      get: (sessionId: string, tabId: string, includeScreenshot = true) =>
        this.transport.call("browser.snapshots.get", {
          session_id: sessionId,
          tab_id: tabId,
          include_screenshot: includeScreenshot,
        }),
    },
  };

  readonly trash = {
    list: (input: TrashListInput = {}) => this.transport.call("trash.items.list", input),
    restore: (input: TrashItemActionInput) => this.transport.call("trash.items.restore", input),
    purge: (input: TrashItemActionInput) => this.transport.call("trash.items.purge", input),
    purgeAll: (input: TrashPurgeAllInput) => this.transport.call("trash.items.purge_all", input),
  };

  readonly knowledge = {
    overview: (projectId: string) =>
      this.transport.call("knowledge.projects.overview", { project_id: projectId }),
    listItems: (input: KnowledgeItemListInput) =>
      this.transport.call("knowledge.items.list", input),
    graph: (projectId: string) =>
      this.transport.call("knowledge.graph.get", { project_id: projectId }),
    listSources: (projectId: string) =>
      this.transport.call("knowledge.sources.list", { project_id: projectId }),
    listCollections: (projectId: string) =>
      this.transport.call("knowledge.collections.list", { project_id: projectId }),
    getSnapshot: (taskId: string, snapshotId: string) =>
      this.transport.call("knowledge.snapshots.get", {
        task_id: taskId,
        snapshot_id: snapshotId,
      }),
    getManifest: (taskId: string, manifestId: string) =>
      this.transport.call("harness.manifests.get", {
        task_id: taskId,
        manifest_id: manifestId,
      }),
    search: (input: CoreMethodMap["knowledge.search"]["params"]) =>
      this.transport.call("knowledge.search", input),
    read: (input: CoreMethodMap["knowledge.read"]["params"]) =>
      this.transport.call("knowledge.read", input),
    links: (input: CoreMethodMap["knowledge.links"]["params"]) =>
      this.transport.call("knowledge.links", input),
    sync: {
      start: (input: KnowledgeSyncStartInput) =>
        this.transport.call("knowledge.sync.start", input),
      get: (runId: string) =>
        this.transport.call("knowledge.sync.get", { run_id: runId }),
      cancel: (runId: string) =>
        this.transport.call("knowledge.sync.cancel", { run_id: runId }),
    },
  };

  readonly obsidian = {
    selectVault: () => {
      if (this.transport.selectObsidianVault === undefined) {
        throw new Error("Obsidian Vault selection requires the local Fairy desktop host");
      }
      return this.transport.selectObsidianVault();
    },
    health: () => this.transport.call("obsidian.health.get", {}),
    createSource: (input: ObsidianSourceCreateInput) =>
      this.transport.call("obsidian.sources.create", input),
    listSources: (projectId: string) =>
      this.transport.call("obsidian.sources.list", { project_id: projectId }),
    listItems: (sourceId: string) =>
      this.transport.call("obsidian.sources.items.list", { source_id: sourceId }),
    readItem: (input: ObsidianVaultItemReadInput, options: CoreCallOptions = {}) =>
      this.transport.call("obsidian.sources.items.read", input, options),
    sync: (input: ObsidianSourceSyncInput) =>
      this.transport.call("obsidian.sync.start", input),
  };

  readonly conversations = {
    create: (input: ConversationCreateInput) => this.transport.call("conversations.create", input),
    get: (conversationId: string) => this.transport.call("conversations.get", { conversation_id: conversationId }),
    list: (input: ConversationListInput = {}) => this.transport.call("conversations.list", input),
    update: (input: ConversationUpdateInput) => this.transport.call("conversations.update", input),
    delete: (input: ConversationDeleteInput) => this.transport.call("conversations.delete", input),
    moveToProject: (input: ConversationMoveToProjectInput) =>
      this.transport.call("conversations.move_to_project", input),
  };

  readonly tasks = {
    archive: (input: TaskArchiveInput) => this.transport.call("tasks.archive", input),
    create: (input: TaskCreateInput) => this.transport.call("tasks.create", input),
    get: (taskId: string) => this.transport.call("tasks.get", { task_id: taskId }),
    list: (input: TaskListInput = {}) => this.transport.call("tasks.list", input),
    review: (taskId: string) => this.transport.call("tasks.review", { task_id: taskId }),
    updateMetadata: (input: TaskMetadataUpdateInput) => this.transport.call("tasks.update_metadata", input),
  };

  readonly changesets = {
    propose: (input: ChangesetProposal) => this.transport.call("changesets.propose", input),
  };

  readonly approvals = {
    decide: (input: ApprovalDecisionInput) => this.transport.call("approvals.decide", input),
    list: (input: ApprovalListInput = {}) => this.transport.call("approvals.list", input),
  };

  readonly versions = {
    get: (versionId: string) => this.transport.call("versions.get", { version_id: versionId }),
    list: (input: VersionListInput = {}) => this.transport.call("versions.list", input),
    accept: (input: VersionAcceptInput) => this.transport.call("versions.accept", input),
    discard: (taskId: string) => this.transport.call("versions.discard", { task_id: taskId }),
  };

  readonly workspaces = {
    get: (workspaceId: string) => this.transport.call("workspaces.get", { workspace_id: workspaceId }),
    listFiles: (input: WorkspaceVersionInput) => this.transport.call("workspaces.files.list", input),
    readFile: (input: WorkspaceFileReadInput) => this.transport.call("workspaces.files.read", input),
    openStream: (input: WorkspaceFileStreamInput) => this.transport.call("files.open_stream", input),
    mutateFiles: (input: WorkspaceFileMutateInput) => this.transport.call("workspaces.files.mutate", input),
    export: (input: WorkspaceExportInput) => this.transport.call("workspaces.export", input),
  };

  readonly files = {
    probe: (input: WorkspaceFileReadInput) => this.transport.call("files.probe", input),
    present: (input: FilePresentInput) => this.transport.call("files.present", input),
    compare: (input: FileCompareInput) => this.transport.call("files.compare", input),
    cancel: (input: FileRenderJobCancelInput) => this.transport.call("files.cancel", input),
    openStream: (input: WorkspaceFileStreamInput) => this.transport.call("files.open_stream", input),
  };

  readonly assetSets = {
    create: (input: AssetSetCreateInput) => this.transport.call("asset_sets.create", input),
    list: (input: WorkspaceVersionInput) => this.transport.call("asset_sets.list", input),
  };

  readonly rendererPacks = {
    list: () => this.transport.call("renderer_packs.list", {}),
    health: () => this.transport.call("renderer_packs.health", {}),
    install: (input: RendererPackInstallInput) => this.transport.call("renderer_packs.install", input),
    update: (input: RendererPackInstallInput) => this.transport.call("renderer_packs.update", input),
    remove: (input: RendererPackRemoveInput) => this.transport.call("renderer_packs.remove", input),
  };

  readonly annotations = {
    list: (input: AnnotationListInput) => this.transport.call("annotations.list", input),
    update: (input: AnnotationUpdateInput) => this.transport.call("annotations.update", input),
  };

  readonly selections = {
    create: (input: SelectionCreateInput) => this.transport.call("selections.create", input),
  };

  readonly editRecipes = {
    create: (input: EditRecipeCreateInput) => this.transport.call("edit_recipes.create", input),
    update: (input: EditRecipeUpdateInput) => this.transport.call("edit_recipes.update", input),
    apply: (input: EditRecipeApplyInput) => this.transport.call("edit_recipes.apply", input),
    discard: (recipeId: string) => this.transport.call("edit_recipes.discard", { recipe_id: recipeId }),
  };

  readonly fileSets = {
    get: (input: FileSetGetInput) => this.transport.call("file_sets.get", input),
    resolve: (input: FileSetResolveInput) => this.transport.call("file_sets.resolve", input),
  };

  readonly capabilities = {
    get: () => this.transport.call("capabilities.get", {}),
  };

  readonly permissions = {
    get: () => this.transport.call("permissions.get", {}),
    update: (input: ExecutionSettingsUpdateInput) => this.transport.call("permissions.update", input),
  };

  readonly providers = {
    list: () => this.transport.call("providers.list", {}),
    health: (profileId?: string) =>
      this.transport.call(
        "providers.health",
        profileId === undefined ? {} : ({ profile_id: profileId } satisfies ProviderHealthInput),
      ),
    openRouterStatus: () => this.requireProviderHost("status")(),
    configureOpenRouter: (input: OpenRouterConfigurationInput) => this.requireProviderHost("configure")(input),
    deleteOpenRouter: () => this.requireProviderHost("delete")(),
    realtimeStatus: (provider: RealtimeCredentialProvider) => {
      if (this.transport.providerRealtimeStatus === undefined) {
        throw new Error("Realtime credentials require the Fairy desktop host");
      }
      return this.transport.providerRealtimeStatus(provider);
    },
    configureRealtime: (input: RealtimeProviderCredentialInput) => {
      if (this.transport.providerRealtimeConfigure === undefined) {
        throw new Error("Realtime credentials require the Fairy desktop host");
      }
      return this.transport.providerRealtimeConfigure(input);
    },
    deleteRealtime: (provider: RealtimeCredentialProvider) => {
      if (this.transport.providerRealtimeDelete === undefined) {
        throw new Error("Realtime credentials require the Fairy desktop host");
      }
      return this.transport.providerRealtimeDelete(provider);
    },
  };

  readonly models = {
    catalog: {
      list: () => this.transport.call("models.catalog.list", {}),
      refresh: () => this.transport.call("models.catalog.refresh", {}),
    },
    selection: {
      get: () => this.transport.call("models.selection.get", {}),
      update: (input: ModelSelectionUpdateInput) =>
        this.transport.call("models.selection.update", input),
    },
  };

  readonly media = {
    jobs: {
      list: (taskId: string) => this.transport.call("media.jobs.list", { task_id: taskId }),
    },
    images: {
      generate: (input: MediaImageGenerateInput) =>
        this.transport.call("media.images.generate", input),
    },
    audio: {
      generate: (input: MediaAudioGenerateInput) =>
        this.transport.call("media.audio.generate", input),
    },
    videos: {
      start: (input: MediaVideoStartInput) =>
        this.transport.call("media.videos.start", input),
      get: (jobId: string) => this.transport.call("media.videos.get", { job_id: jobId }),
      cancel: (input: MediaVideoCancelInput) =>
        this.transport.call("media.videos.cancel", input),
    },
  };

  readonly skills = {
    catalog: () => this.transport.call("extensions.catalog.list", {}),
    install: (input: SkillInstallInput) => this.transport.call("skills.install", input),
    inspectImport: (input: SkillImportInspectInput) =>
      this.transport.call("skills.import.inspect", input),
    installImport: (input: SkillImportInstallInput) =>
      this.transport.call("skills.import.install", input),
    create: (input: SkillCreateInput) => this.transport.call("skills.create", input),
    list: () => this.transport.call("skills.list", {}),
    remove: (input: SkillRemoveInput) => this.transport.call("skills.remove", input),
    setEnabled: (input: SkillSetEnabledInput) =>
      this.transport.call("skills.set_enabled", input),
    update: (input: SkillUpdateInput) => this.transport.call("skills.update", input),
  };

  readonly mcp = {
    installPreset: (input: McpPresetInstallInput) =>
      this.transport.call("mcp.presets.install", input),
    servers: {
      list: () => this.transport.call("mcp.servers.list", {}),
      configure: (input: McpServerConfigureInput) => this.transport.call("mcp.servers.configure", input),
      discover: (input: McpServerDiscoverInput) => this.transport.call("mcp.servers.discover", input),
      accept: (input: McpServerAcceptInput) => this.transport.call("mcp.servers.accept", input),
      setEnabled: (input: McpServerSetEnabledInput) => this.transport.call("mcp.servers.set_enabled", input),
      delete: (input: McpServerDeleteInput) => this.transport.call("mcp.servers.delete", input),
    },
  };

  readonly runtimes = {
    get: (runtimeId: string) => this.transport.call("runtimes.get", { runtime_id: runtimeId }),
    health: (taskId: string) => this.transport.call("runtimes.health", { task_id: taskId }),
  };

  readonly systemActions = {
    execute: (input: SystemActionRequest) => this.transport.call("system.actions.execute", input),
  };

  readonly previews = {
    activate: (input: PreviewActivateInput) => this.transport.call("previews.activate", input),
    start: (input: PreviewStartInput) => this.transport.call("previews.start", input),
    get: (previewId: string) => this.transport.call("previews.get", { preview_id: previewId }),
    resolve: (input: PreviewResolveInput) => this.transport.call("previews.resolve", input),
    stop: (input: PreviewStopInput) => this.transport.call("previews.stop", input),
  };

  readonly artifacts = {
    list: (taskId: string) => this.transport.call("artifacts.list", { task_id: taskId }),
    read: (artifactId: string) => this.transport.call("artifacts.read", { artifact_id: artifactId }),
  };

  readonly documents = {
    import: (input: DocumentImportInput) => this.transport.call("documents.import", input),
    list: (input: DocumentListInput) => this.transport.call("documents.list", input),
    get: (taskId: string, documentId: string) =>
      this.transport.call("documents.get", {
        task_id: taskId,
        document_id: documentId,
      }),
    search: (input: DocumentSearchInput) => this.transport.call("documents.search", input),
    delete: (input: DocumentDeleteInput) => this.transport.call("documents.delete", input),
  };

  readonly assistant = {
    messages: {
      submit: (input: AssistantMessageSubmitInput) =>
        this.transport.call("assistant.messages.submit", input),
      cancel: (input: AssistantMessageCancelInput) =>
        this.transport.call("assistant.messages.cancel", input),
    },
    commands: {
      dispatch: (input: AssistantCommandInput) =>
        this.transport.call("assistant.commands.dispatch", input),
    },
    backgroundTasks: {
      list: (currentConversationId?: string | null) =>
        this.transport.call("assistant.background_tasks.list", {
          current_conversation_id: currentConversationId,
          recent_limit: 20,
        }),
    },
    schedules: {
      create: (input: AssistantScheduleCreateInput) =>
        this.transport.call("assistant.schedules.create", input),
      get: (scheduleId: string) =>
        this.transport.call("assistant.schedules.get", { schedule_id: scheduleId }),
      list: (conversationId?: string | null) =>
        this.transport.call("assistant.schedules.list", {
          conversation_id: conversationId,
          limit: 100,
        }),
      update: (input: AssistantScheduleUpdateInput) =>
        this.transport.call("assistant.schedules.update", input),
      pause: (scheduleId: string, expectedRevision: number) =>
        this.transport.call("assistant.schedules.pause", {
          schedule_id: scheduleId,
          expected_revision: expectedRevision,
        }),
      resume: (scheduleId: string, expectedRevision: number) =>
        this.transport.call("assistant.schedules.resume", {
          schedule_id: scheduleId,
          expected_revision: expectedRevision,
        }),
      cancel: (scheduleId: string, expectedRevision: number) =>
        this.transport.call("assistant.schedules.cancel", {
          schedule_id: scheduleId,
          expected_revision: expectedRevision,
        }),
      runNow: (scheduleId: string, expectedRevision: number, idempotencyKey: string) =>
        this.transport.call("assistant.schedules.run_now", {
          schedule_id: scheduleId,
          expected_revision: expectedRevision,
          idempotency_key: idempotencyKey,
        }),
    },
    turns: {
      create: (input: AssistantTurnCreateInput) => this.transport.call("assistant.turns.create", input),
      get: (turnId: string) => this.transport.call("assistant.turns.get", { turn_id: turnId }),
      getInterpretation: (input: AssistantTurnInterpretationInput) =>
        this.transport.call("assistant.turns.interpretation.get", input),
      pause: (turnId: string) => this.transport.call("assistant.turns.pause", { turn_id: turnId }),
      cancel: (input: AssistantTurnCancelInput) => this.transport.call("assistant.turns.cancel", input),
      run: (turnId: string) => this.transport.call("assistant.turns.run", { turn_id: turnId }),
      start: (turnId: string) => this.transport.call("assistant.turns.start", { turn_id: turnId }),
      retry: (input: AssistantTurnRetryInput) => this.transport.call("assistant.turns.retry", input),
      resume: (turnId: string) => this.transport.call("assistant.turns.resume", { turn_id: turnId }),
      respond: (input: AssistantTurnRespondInput) =>
        this.transport.call("assistant.turns.respond", input),
      steer: (input: AssistantTurnSteerInput) => this.transport.call("assistant.turns.steer", input),
      trace: (turnId: string) =>
        this.transport.call("assistant.turns.trace.list", { turn_id: turnId }),
      workflow: {
        get: (turnId: string) =>
          this.transport.call("assistant.turns.workflow.get", { turn_id: turnId }),
      },
    },
  };

  readonly messages = {
    list: (input: MessageListInput) => this.transport.call("messages.list", input),
  };

  readonly voice = {
    transcribe: (input: VoiceTranscribeInput) => this.transport.call("voice.transcribe", input),
    synthesize: (input: VoiceSynthesizeInput, signal?: AbortSignal) =>
      this.transport.call("voice.synthesize", input, { signal }),
    sessions: {
      start: (input: VoiceSessionStartInput) => this.transport.call("voice.sessions.start", input),
      get: (sessionId: string) => this.transport.call("voice.sessions.get", { session_id: sessionId }),
      cancel: (sessionId: string) => this.transport.call("voice.sessions.cancel", { session_id: sessionId }),
    },
  };

  readonly realtime = {
    persona: {
      snapshot: (input: RealtimePersonaSnapshotInput) =>
        this.transport.call("realtime.persona.snapshot", input),
    },
    sessions: {
      start: (input: RealtimeSessionStartInput) =>
        this.transport.call("realtime.sessions.start", input),
      get: (sessionId: string) =>
        this.transport.call("realtime.sessions.get", { session_id: sessionId }),
      list: (limit = 50) => this.transport.call("realtime.sessions.list", { limit }),
      report: (input: RealtimeSessionReportInput) =>
        this.transport.call("realtime.sessions.report", input),
      stop: (input: RealtimeSessionStopInput) =>
        this.transport.call("realtime.sessions.stop", input),
    },
    assistance: {
      get: (input: RealtimeAssistanceGetInput) =>
        this.transport.call("realtime.assistance.get", input),
      cancel: (input: RealtimeAssistanceCancelInput) =>
        this.transport.call("realtime.assistance.cancel", input),
    },
    digests: {
      create: (input: CompanionDigestCreateInput) =>
        this.transport.call("realtime.digests.create", input),
      get: (digestId: string) =>
        this.transport.call("realtime.digests.get", { digest_id: digestId }),
      list: (input: CompanionDigestListInput = {}) =>
        this.transport.call("realtime.digests.list", input),
    },
    memoryProposals: {
      list: (input: RealtimeMemoryProposalListInput = {}) =>
        this.transport.call("realtime.memory-proposals.list", input),
      accept: (input: RealtimeMemoryProposalActionInput) =>
        this.transport.call("realtime.memory-proposals.accept", input),
      reject: (input: RealtimeMemoryProposalActionInput) =>
        this.transport.call("realtime.memory-proposals.reject", input),
    },
    memories: {
      save: (input: GameMemorySaveInput) =>
        this.transport.call("realtime.memories.save", input),
      list: (limit = 50) => this.transport.call("realtime.memories.list", { limit }),
      delete: (memoryId: string) =>
        this.transport.call("realtime.memories.delete", { memory_id: memoryId }),
    },
    transcript: {
      append: (input: RealtimeTranscriptAppendInput) =>
        this.transport.call("realtime.transcript.append", input),
      list: (input: RealtimeTranscriptListInput) =>
        this.transport.call("realtime.transcript.list", input),
    },
    worker: {
      preview: (input: RealtimeBackendResolutionInput) => {
        if (!this.transport.realtimeBackendResolutionPreview) {
          throw new Error("Realtime requires Fairy desktop");
        }
        return this.transport.realtimeBackendResolutionPreview(input);
      },
      status: () => {
        if (!this.transport.realtimeWorkerStatus) throw new Error("Realtime requires Fairy desktop");
        return this.transport.realtimeWorkerStatus();
      },
      start: (input: RealtimeWorkerStartInput) => {
        if (!this.transport.realtimeWorkerStart) throw new Error("Realtime requires Fairy desktop");
        return this.transport.realtimeWorkerStart(input);
      },
      continue: (input: RealtimeWorkerStartInput) => {
        if (!this.transport.realtimeWorkerContinue) {
          throw new Error("Realtime requires Fairy desktop");
        }
        return this.transport.realtimeWorkerContinue(input);
      },
      stop: (sessionId: string) => {
        if (!this.transport.realtimeWorkerStop) throw new Error("Realtime requires Fairy desktop");
        return this.transport.realtimeWorkerStop(sessionId);
      },
      toolResult: (input: RealtimeWorkerToolResultInput) => {
        if (!this.transport.realtimeWorkerToolResult) throw new Error("Realtime requires Fairy desktop");
        return this.transport.realtimeWorkerToolResult(input);
      },
      setInput: (input: RealtimeWorkerSetInputInput) => {
        if (!this.transport.realtimeWorkerSetInput) throw new Error("Realtime requires Fairy desktop");
        return this.transport.realtimeWorkerSetInput(input);
      },
      setPolicy: (input: RealtimeWorkerSetPolicyInput) => {
        if (!this.transport.realtimeWorkerSetPolicy) {
          throw new Error("Realtime requires Fairy desktop");
        }
        return this.transport.realtimeWorkerSetPolicy(input);
      },
      retryMedia: (input: RealtimeWorkerRetryMediaInput) => {
        if (!this.transport.realtimeWorkerRetryMedia) {
          throw new Error("Realtime requires Fairy desktop");
        }
        return this.transport.realtimeWorkerRetryMedia(input);
      },
      replaceSource: (input: RealtimeWorkerReplaceSourceInput) => {
        if (!this.transport.realtimeWorkerReplaceSource) {
          throw new Error("Realtime requires Fairy desktop");
        }
        return this.transport.realtimeWorkerReplaceSource(input);
      },
      wake: (input: RealtimeWorkerWakeInput) => {
        if (!this.transport.realtimeWorkerWake) throw new Error("Realtime requires Fairy desktop");
        return this.transport.realtimeWorkerWake(input);
      },
      pausePrivacy: (input: RealtimeWorkerWakeInput) => {
        if (!this.transport.realtimeWorkerPausePrivacy) {
          throw new Error("Realtime requires Fairy desktop");
        }
        return this.transport.realtimeWorkerPausePrivacy(input);
      },
      resumePrivacy: (input: RealtimeWorkerWakeInput) => {
        if (!this.transport.realtimeWorkerResumePrivacy) {
          throw new Error("Realtime requires Fairy desktop");
        }
        return this.transport.realtimeWorkerResumePrivacy(input);
      },
      extend: (input: RealtimeWorkerExtendInput) => {
        if (!this.transport.realtimeWorkerExtend) throw new Error("Realtime requires Fairy desktop");
        return this.transport.realtimeWorkerExtend(input);
      },
      speechState: (input: RealtimeWorkerSpeechStateInput) => {
        if (!this.transport.realtimeWorkerSpeechState) {
          throw new Error("Realtime requires Fairy desktop");
        }
        return this.transport.realtimeWorkerSpeechState(input);
      },
    },
  };

  readonly memory = {
    observations: {
      create: (input: MemoryObserveInput) => this.transport.call("memory.observations.create", input),
      list: (taskId: string, namespace: MemoryNamespace) =>
        this.transport.call("memory.observations.list", {
          task_id: taskId,
          namespace,
        }),
    },
    claims: {
      promote: (input: MemoryClaimPromoteInput) => this.transport.call("memory.claims.promote", input),
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
      supersede: (input: MemoryClaimSupersedeInput) => this.transport.call("memory.claims.supersede", input),
      resolveConflict: (input: MemoryClaimResolveInput) => this.transport.call("memory.claims.resolve_conflict", input),
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
      health: (taskId: string) => this.transport.call("memory.projection.health", { task_id: taskId }),
    },
    proposals: {
      list: (taskId: string, limit = 100) =>
        this.transport.call("memory.proposals.list", { task_id: taskId, limit }),
      accept: (input: MemoryProposalActionInput) =>
        this.transport.call("memory.proposals.accept", input),
      reject: (input: MemoryProposalActionInput) =>
        this.transport.call("memory.proposals.reject", input),
    },
    settings: {
      get: () => this.transport.call("memory.settings.get", {}),
      update: (input: MemorySettingsUpdateInput) =>
        this.transport.call("memory.settings.update", input),
    },
    forget: (input: MemoryForgetInput) => this.transport.call("memory.forget", input),
  };

  readonly events = {
    sourceId: () => this.transport.eventSourceId ?? "core:default",
    state: (options: CoreCallOptions = {}): Promise<EventStreamState> =>
      this.transport.call("events.state", {}, options),
    list: (cursor = 0, limit = 500, options: CoreCallOptions = {}) =>
      this.transport.call("events.list", { cursor, limit }, options),
    subscribe: (cursor = 0, options: EventSubscriptionOptions = {}) => this.subscribeToEvents(cursor, options),
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

  private subscribeToEvents(cursor: number, options: EventSubscriptionOptions): AsyncIterable<EventEnvelope> {
    return this.transport.subscribeEvents?.(cursor, options) ?? pollCoreEvents(this.transport, cursor, options);
  }
}

export async function* pollCoreEvents(
  transport: CoreTransport,
  cursor: number,
  options: EventSubscriptionOptions,
): AsyncIterable<EventEnvelope> {
  let current = cursor;
  const requestedInterval = options.pollIntervalMs ?? DEFAULT_EVENT_POLL_MS;
  const baseInterval = Number.isFinite(requestedInterval)
    ? Math.max(1, Math.min(MAX_IDLE_EVENT_POLL_MS, requestedInterval))
    : DEFAULT_EVENT_POLL_MS;
  let idleInterval = baseInterval;
  while (!options.signal?.aborted) {
    const batch = await transport.call("events.subscribe", { cursor: current }, { signal: options.signal });
    if (options.signal?.aborted) return;
    let progressed = false;
    for (const event of batch.items) {
      if (options.signal?.aborted) return;
      if (event.cursor <= current) continue;
      current = event.cursor;
      progressed = true;
      yield event;
    }
    if (!progressed) {
      await waitForPoll(idleInterval, options.signal);
      idleInterval = Math.min(MAX_IDLE_EVENT_POLL_MS, idleInterval * 2);
    } else {
      idleInterval = baseInterval;
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
