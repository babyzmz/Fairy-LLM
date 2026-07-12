import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

import type { DesktopPreferences } from "../settings/client";

export interface PetPreferencePatch {
  expected_revision: number;
  voice_auto_play_pet?: boolean;
  pet_muted?: boolean;
  pet_always_on_top?: boolean;
}

export interface PetHost {
  getPreferences(): Promise<DesktopPreferences>;
  updatePreferences(input: PetPreferencePatch): Promise<DesktopPreferences>;
  onPreferences(listener: (preferences: DesktopPreferences) => void): Promise<() => void>;
  setExpanded(expanded: boolean): Promise<void>;
  openSettings(): Promise<void>;
  exit(): Promise<void>;
}

export function createDefaultPetHost(): PetHost {
  if (!isTauri()) return createBrowserPetHost();
  return {
    getPreferences: () => invoke("desktop_preferences_get"),
    updatePreferences: (input) => invoke("pet_preferences_update", { input }),
    async onPreferences(listener) {
      return listen<DesktopPreferences>("desktop-preferences-changed", (event) => {
        listener(event.payload);
      });
    },
    setExpanded: (expanded) => invoke("pet_window_set_expanded", { expanded }),
    openSettings: () => invoke("open_settings_window"),
    exit: () => invoke("pet_exit"),
  };
}

function createBrowserPetHost(): PetHost {
  let preferences = browserPreferences();
  const listeners = new Set<(value: DesktopPreferences) => void>();
  return {
    async getPreferences() {
      return preferences;
    },
    async updatePreferences(input) {
      preferences = {
        ...preferences,
        revision: preferences.revision + 1,
        ...(input.voice_auto_play_pet === undefined
          ? {}
          : { voice_auto_play_pet: input.voice_auto_play_pet }),
        ...(input.pet_muted === undefined ? {} : { pet_muted: input.pet_muted }),
        ...(input.pet_always_on_top === undefined
          ? {}
          : { pet_always_on_top: input.pet_always_on_top }),
      };
      for (const listener of listeners) listener(preferences);
      return preferences;
    },
    async onPreferences(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    async setExpanded() {},
    async openSettings() {},
    async exit() {},
  };
}

function browserPreferences(): DesktopPreferences {
  return {
    schema_version: 1,
    revision: 0,
    language: "system",
    launch_at_startup: false,
    minimize_to_tray: true,
    theme: "system",
    reduced_motion: false,
    compact_density: false,
    selected_profile_id: null,
    voice_auto_play_chat: false,
    voice_auto_play_pet: true,
    voice_volume_percent: 80,
    voice_rate_percent: 100,
    permission_cloud_profile: "standard",
    memory_enabled: true,
    memory_retention_days: 90,
    analytics_enabled: false,
    pet_enabled: true,
    pet_always_on_top: true,
    pet_muted: false,
    developer_mode: false,
  };
}
