import type { Dispatch, SetStateAction } from "react";

import type {
  CapabilityManifest,
  ExecutionSettings,
  ExtensionCatalogEntry,
  McpServer,
  MemorySettings,
  ModelCatalogPage,
  ModelSelectionPreference,
  ProjectArchivedItem,
  Skill,
  SettingsCategoryId,
  TrashItem,
} from "../core/client";
import type { PresenceRendererHealth } from "../presence/transport/rendererHealth";
import type {
  DesktopPreferences,
  SettingsClient,
  VoiceWorkerHealth,
} from "./client";
import type { KnowledgePrivacyData } from "./KnowledgePrivacyPanel";

export interface RealtimeCredentialView {
  configured: boolean;
  /** Last four characters of the stored key, when configured and readable. */
  hint: string | null;
  /** The status lookup failed (e.g. a stored key that cannot be decrypted) — distinct from "not configured". */
  error: boolean;
}

export interface SettingsData extends KnowledgePrivacyData {
  preferences: DesktopPreferences;
  memorySettings: MemorySettings;
  openRouterConfigured: boolean;
  openRouterAccountId: string | null;
  realtimeCredentials: Record<"gemini" | "zhipu", RealtimeCredentialView>;
  modelCatalog: ModelCatalogPage;
  modelSelection: ModelSelectionPreference;
  permissions: ExecutionSettings;
  capabilities: CapabilityManifest;
  skills: Skill[];
  extensionCatalog: ExtensionCatalogEntry[];
  servers: McpServer[];
  latestTaskId: string | null;
  voiceHealth: VoiceWorkerHealth;
  rendererHealth: PresenceRendererHealth;
  archivedProjects: ProjectArchivedItem[];
  trashItems: TrashItem[];
  autoPurgeError: string | null;
}

export interface SettingsCategoryProps {
  id: SettingsCategoryId;
  data: SettingsData;
  client: SettingsClient;
  busy: boolean;
  act(operation: () => Promise<void>): Promise<void>;
  reload(): Promise<void>;
  updatePreferences(patch: Partial<DesktopPreferences>): Promise<void>;
  updateMemorySettings(
    patch: Partial<
      Pick<
        MemorySettings,
        "enabled" | "retention_days" | "export_to_obsidian" | "sync_normalized_content"
      >
    >,
  ): Promise<void>;
  updateData: Dispatch<SetStateAction<SettingsData | null>>;
}

export function titleCase(value: string) {
  return `${value.charAt(0).toUpperCase()}${value.slice(1)}`;
}

export function messageOf(value: unknown) {
  if (value instanceof Error) return value.message;
  if (typeof value === "string" && value.trim() !== "") return value;
  if (
    typeof value === "object" &&
    value !== null &&
    "message" in value &&
    typeof value.message === "string"
  ) {
    return value.message;
  }
  return "Settings request failed";
}

export function formatBytes(value: number) {
  if (value < 1_024) return `${value} B`;
  if (value < 1_048_576) return `${(value / 1_024).toFixed(1)} KiB`;
  if (value < 1_073_741_824) return `${(value / 1_048_576).toFixed(1)} MiB`;
  return `${(value / 1_073_741_824).toFixed(1)} GiB`;
}
