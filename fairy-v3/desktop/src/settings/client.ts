import type {
  CapabilityManifest,
  CoreCallOptions,
  CoreMethodMap,
  CoreMethodName,
  ExecutionSettings,
  ExtensionCatalogPage,
  KnowledgeSourcePage,
  McpServerAcceptInput,
  McpServerConfigureInput,
  McpServerDeleteInput,
  McpServerDiscoverInput,
  McpServerSetEnabledInput,
  McpPresetInstallInput,
  ModelCatalogPage,
  ModelSelectionPreference,
  ModelSelectionUpdateInput,
  MemoryProposalActionInput,
  MemoryProposalPage,
  MemorySettings,
  MemorySettingsUpdateInput,
  OpenRouterConfigurationInput,
  OpenRouterConfigurationStatus,
  ObsidianConnectorHealth,
  ProviderHealthPage,
  ProviderProfilePage,
  RealtimeCredentialProvider,
  RealtimeProviderCredentialInput,
  RealtimeProviderCredentialStatus,
  ProjectArchiveInput,
  ProjectArchivedListInput,
  ProjectArchivedPage,
  ProjectDeleteInput,
  ProjectPage,
  SkillPage,
  SkillCreateInput,
  SkillImportInspectInput,
  SkillImportInstallInput,
  SkillInstallInput,
  SkillRemoveInput,
  SkillSetEnabledInput,
  SkillUpdateInput,
  TaskPage,
  TrashItemActionInput,
  TrashItemPage,
  TrashListInput,
  TrashMutationResult,
  TrashPurgeAllInput,
  TrashPurgeResult,
  McpServerPage,
} from "../core/client";
import { CoreRpcError, type InvokeFunction } from "../core/tauriTransport";
import type { PresenceRendererHealth } from "../presence/transport/rendererHealth";

export type ThemePreference = "system" | "dark" | "light";
export type PetRendererMode = "auto" | "liquid" | "compatibility";
export type PetOpticsMode = "standard" | "enhanced";
export const DESKTOP_PREFERENCES_EVENT = "fairy-desktop-preferences";

export interface PetAnchorPreference {
  monitor_id: string;
  x_ratio: number;
  y_ratio: number;
}

export interface DesktopPreferences {
  schema_version: number;
  revision: number;
  language: "system" | "en" | "zh-CN";
  launch_at_startup: boolean;
  minimize_to_tray: boolean;
  theme: ThemePreference;
  reduced_motion: boolean;
  compact_density: boolean;
  selected_profile_id: string | null;
  voice_auto_play_chat: boolean;
  voice_auto_play_pet: boolean;
  voice_volume_percent: number;
  voice_rate_percent: number;
  permission_cloud_profile: "observe" | "standard" | "autonomous";
  analytics_enabled: boolean;
  realtime_provider: "auto" | "gemini_live" | "glm_realtime_flash" | "glm_realtime_air";
  realtime_voice_mode: "native" | "fairy";
  realtime_game_audio_default: boolean;
  realtime_memory_enabled: boolean;
  realtime_max_session_minutes: number;
  trash_auto_purge_30_days: boolean;
  pet_enabled: boolean;
  pet_always_on_top: boolean;
  pet_muted: boolean;
  pet_size_percent: number;
  pet_opacity_percent: number;
  pet_motion_enabled: boolean;
  pet_particles_enabled: boolean;
  pet_hover_enabled: boolean;
  pet_hover_dwell_ms: number;
  pet_do_not_disturb: boolean;
  pet_remember_position: boolean;
  pet_renderer_mode: PetRendererMode;
  pet_optics_mode: PetOpticsMode;
  pet_target_fps: 60 | 144 | 300;
  pet_anchor: PetAnchorPreference | null;
  developer_mode: boolean;
}

export interface VoiceWorkerHealth {
  status: "ready" | "warming" | "model_missing" | "prompt_missing" | "cuda_unavailable" | "acceleration_unavailable" | "error" | "unavailable";
  model_repository: string;
  model_installed: boolean;
  model_ready: boolean;
  model_digest: string | null;
  prompt_ready: boolean;
  cuda_available: boolean;
  tensorrt_available: boolean;
  backend: string | null;
  device_name: string | null;
  sample_rate: number;
  error_code: string | null;
}

export interface VoiceModelInstallResult {
  installed: boolean;
  manifest_digest: string;
}

interface JsonRpcSuccess<T> {
  jsonrpc: "2.0";
  id: number;
  result: T;
}

interface JsonRpcFailure {
  jsonrpc: "2.0";
  id: number;
  error: {
    code: number;
    message: string;
    data?: { error_code?: string; [key: string]: unknown };
  };
}

type JsonRpcResponse<T> = JsonRpcSuccess<T> | JsonRpcFailure;

export class SettingsClient {
  private requestId = 0;

  readonly preferences = {
    get: () => this.invoke<DesktopPreferences>("desktop_preferences_get"),
    update: (preferences: DesktopPreferences) =>
      this.invoke<DesktopPreferences>("desktop_preferences_update", {
        input: {
          expected_revision: preferences.revision,
          preferences,
        },
      }),
  };

  readonly providers = {
    list: () => this.call("providers.list", {}) as Promise<ProviderProfilePage>,
    health: () => this.call("providers.health", {}) as Promise<ProviderHealthPage>,
    openRouterStatus: () =>
      this.invoke<OpenRouterConfigurationStatus>("provider_openrouter_status"),
    configureOpenRouter: (input: OpenRouterConfigurationInput) =>
      this.invoke<OpenRouterConfigurationStatus>("provider_openrouter_configure", { input }),
    deleteOpenRouter: () =>
      this.invoke<OpenRouterConfigurationStatus>("provider_openrouter_delete"),
    realtimeStatus: (provider: RealtimeCredentialProvider) =>
      this.invoke<RealtimeProviderCredentialStatus>("provider_realtime_status", {
        input: { provider },
      }),
    configureRealtime: (input: RealtimeProviderCredentialInput) =>
      this.invoke<RealtimeProviderCredentialStatus>("provider_realtime_configure", {
        input,
      }),
    deleteRealtime: (provider: RealtimeCredentialProvider) =>
      this.invoke<RealtimeProviderCredentialStatus>("provider_realtime_delete", {
        input: { provider },
      }),
  };

  readonly models = {
    catalog: {
      list: () => this.call("models.catalog.list", {}) as Promise<ModelCatalogPage>,
      refresh: () => this.call("models.catalog.refresh", {}) as Promise<ModelCatalogPage>,
    },
    selection: {
      get: () => this.call("models.selection.get", {}) as Promise<ModelSelectionPreference>,
      update: (input: ModelSelectionUpdateInput) =>
        this.call("models.selection.update", input) as Promise<ModelSelectionPreference>,
    },
  };

  readonly permissions = {
    get: () => this.call("permissions.get", {}) as Promise<ExecutionSettings>,
    update: (input: CoreMethodMap["permissions.update"]["params"]) =>
      this.call("permissions.update", input) as Promise<ExecutionSettings>,
    capabilities: () =>
      this.call("capabilities.get", {}) as Promise<CapabilityManifest>,
  };

  readonly extensions = {
    selectSkillSource: (sourceKind: "folder" | "zip") =>
      this.invoke<string | null>("select_skill_source", { sourceKind }),
    catalog: () =>
      this.call("extensions.catalog.list", {}) as Promise<ExtensionCatalogPage>,
    skills: () => this.call("skills.list", {}) as Promise<SkillPage>,
    installSkill: (input: SkillInstallInput) => this.call("skills.install", input),
    inspectSkillImport: (input: SkillImportInspectInput) =>
      this.call("skills.import.inspect", input),
    installSkillImport: (input: SkillImportInstallInput) =>
      this.call("skills.import.install", input),
    createSkill: (input: SkillCreateInput) => this.call("skills.create", input),
    updateSkill: (input: SkillUpdateInput) => this.call("skills.update", input),
    setSkillEnabled: (input: SkillSetEnabledInput) =>
      this.call("skills.set_enabled", input),
    removeSkill: (input: SkillRemoveInput) => this.call("skills.remove", input),
    servers: () => this.call("mcp.servers.list", {}) as Promise<McpServerPage>,
    installPreset: (input: McpPresetInstallInput) =>
      this.call("mcp.presets.install", input),
    configure: (input: McpServerConfigureInput) =>
      this.call("mcp.servers.configure", input),
    discover: (input: McpServerDiscoverInput) =>
      this.call("mcp.servers.discover", input),
    accept: (input: McpServerAcceptInput) => this.call("mcp.servers.accept", input),
    setEnabled: (input: McpServerSetEnabledInput) =>
      this.call("mcp.servers.set_enabled", input),
    delete: (input: McpServerDeleteInput) => this.call("mcp.servers.delete", input),
  };

  readonly context = {
    latestTask: () =>
      this.call("tasks.list", { limit: 1 }) as Promise<TaskPage>,
    projects: (cursor: string | null = null) =>
      this.call("projects.list", { limit: 100, cursor }) as Promise<ProjectPage>,
    tasks: (cursor: string | null = null) =>
      this.call("tasks.list", { limit: 100, cursor }) as Promise<TaskPage>,
  };

  readonly knowledge = {
    sources: (projectId: string) =>
      this.call("knowledge.sources.list", { project_id: projectId }) as Promise<KnowledgeSourcePage>,
  };

  readonly obsidian = {
    health: () => this.call("obsidian.health.get", {}) as Promise<ObsidianConnectorHealth>,
  };

  readonly projectManagement = {
    archived: {
      list: (input: ProjectArchivedListInput = {}) =>
        this.call("projects.archived.list", input) as Promise<ProjectArchivedPage>,
      restore: (input: ProjectArchiveInput) =>
        this.call("projects.archived.restore", input),
      delete: (input: ProjectDeleteInput) =>
        this.call("projects.archived.delete", input),
    },
    trash: {
      list: (input: TrashListInput = {}) =>
        this.call("trash.items.list", input) as Promise<TrashItemPage>,
      restore: (input: TrashItemActionInput) =>
        this.call("trash.items.restore", input) as Promise<TrashMutationResult>,
      purge: (input: TrashItemActionInput) =>
        this.call("trash.items.purge", input) as Promise<TrashMutationResult>,
      purgeAll: (input: TrashPurgeAllInput) =>
        this.call("trash.items.purge_all", input) as Promise<TrashPurgeResult>,
    },
  };

  readonly memory = {
    settings: {
      get: () => this.call("memory.settings.get", {}) as Promise<MemorySettings>,
      update: (input: MemorySettingsUpdateInput) =>
        this.call("memory.settings.update", input) as Promise<MemorySettings>,
    },
    proposals: {
      list: (taskId: string, limit = 100) =>
        this.call("memory.proposals.list", { task_id: taskId, limit }) as Promise<MemoryProposalPage>,
      accept: (input: MemoryProposalActionInput) =>
        this.call("memory.proposals.accept", input),
      reject: (input: MemoryProposalActionInput) =>
        this.call("memory.proposals.reject", input),
    },
  };

  readonly voice = {
    health: () => this.invoke<VoiceWorkerHealth>("voice_worker_health"),
    installModel: () => this.invoke<VoiceModelInstallResult>("voice_model_install"),
  };

  readonly pet = {
    rendererHealth: () =>
      this.invoke<PresenceRendererHealth | null>("pet_renderer_get_health"),
  };

  constructor(private readonly invoke: InvokeFunction) {}

  private async call<M extends CoreMethodName>(
    method: M,
    params: CoreMethodMap[M]["params"],
    _options: CoreCallOptions = {},
  ): Promise<CoreMethodMap[M]["result"]> {
    const response = await this.invoke<JsonRpcResponse<CoreMethodMap[M]["result"]>>(
      "settings_rpc",
      {
        request: {
          jsonrpc: "2.0",
          id: ++this.requestId,
          method,
          params,
        },
      },
    );
    if ("error" in response) throw new CoreRpcError(response.error);
    return response.result;
  }
}

export function applyDesktopPreferences(preferences: DesktopPreferences): void {
  document.documentElement.dataset.theme = preferences.theme;
  document.documentElement.dataset.reducedMotion = String(preferences.reduced_motion);
  document.documentElement.dataset.density = preferences.compact_density ? "compact" : "comfortable";
  try {
    localStorage.setItem("fairy.workspace.developer", String(preferences.developer_mode));
    localStorage.removeItem("fairy.workspace.provider");
  } catch {
    // Rust preferences remain authoritative when browser storage is unavailable.
  }
  window.dispatchEvent(
    new CustomEvent<DesktopPreferences>(DESKTOP_PREFERENCES_EVENT, {
      detail: preferences,
    }),
  );
}
