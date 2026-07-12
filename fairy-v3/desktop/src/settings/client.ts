import type {
  CapabilityManifest,
  CoreCallOptions,
  CoreMethodMap,
  CoreMethodName,
  ExecutionSettings,
  McpServerAcceptInput,
  McpServerConfigureInput,
  McpServerDeleteInput,
  McpServerDiscoverInput,
  McpServerSetEnabledInput,
  OpenRouterConfigurationInput,
  OpenRouterConfigurationStatus,
  ProviderHealthPage,
  ProviderProfilePage,
  SkillPage,
  McpServerPage,
} from "../core/client";
import { CoreRpcError, type InvokeFunction } from "../core/tauriTransport";

export type ThemePreference = "system" | "dark" | "light";
export const DESKTOP_PREFERENCES_EVENT = "fairy-desktop-preferences";

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
  memory_enabled: boolean;
  memory_retention_days: number;
  analytics_enabled: boolean;
  pet_enabled: boolean;
  pet_always_on_top: boolean;
  pet_muted: boolean;
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
  };

  readonly permissions = {
    get: () => this.call("permissions.get", {}) as Promise<ExecutionSettings>,
    update: (input: CoreMethodMap["permissions.update"]["params"]) =>
      this.call("permissions.update", input) as Promise<ExecutionSettings>,
    capabilities: () =>
      this.call("capabilities.get", {}) as Promise<CapabilityManifest>,
  };

  readonly extensions = {
    skills: () => this.call("skills.list", {}) as Promise<SkillPage>,
    servers: () => this.call("mcp.servers.list", {}) as Promise<McpServerPage>,
    configure: (input: McpServerConfigureInput) =>
      this.call("mcp.servers.configure", input),
    discover: (input: McpServerDiscoverInput) =>
      this.call("mcp.servers.discover", input),
    accept: (input: McpServerAcceptInput) => this.call("mcp.servers.accept", input),
    setEnabled: (input: McpServerSetEnabledInput) =>
      this.call("mcp.servers.set_enabled", input),
    delete: (input: McpServerDeleteInput) => this.call("mcp.servers.delete", input),
  };

  readonly voice = {
    health: () => this.invoke<VoiceWorkerHealth>("voice_worker_health"),
    installModel: () => this.invoke<VoiceModelInstallResult>("voice_model_install"),
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
    if (preferences.selected_profile_id === null) localStorage.removeItem("fairy.workspace.provider");
    else localStorage.setItem("fairy.workspace.provider", preferences.selected_profile_id);
  } catch {
    // Rust preferences remain authoritative when browser storage is unavailable.
  }
  window.dispatchEvent(
    new CustomEvent<DesktopPreferences>(DESKTOP_PREFERENCES_EVENT, {
      detail: preferences,
    }),
  );
}
