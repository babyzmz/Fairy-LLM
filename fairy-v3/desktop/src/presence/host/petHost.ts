import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

import type {
  DesktopPreferences,
  PetAnchorPreference,
} from "../../settings/client";

export interface PetPreferencePatch {
  expected_revision: number;
  voice_auto_play_pet?: boolean;
  pet_muted?: boolean;
  pet_always_on_top?: boolean;
  pet_anchor?: PetAnchorPreference;
}

export interface PetHost {
  getPreferences(): Promise<DesktopPreferences>;
  updatePreferences(input: PetPreferencePatch): Promise<DesktopPreferences>;
  onPreferences(listener: (preferences: DesktopPreferences) => void): Promise<() => void>;
  onInputRequested(listener: () => void): Promise<() => void>;
  onInputToggleRequested(listener: () => void): Promise<() => void>;
  onInputCloseRequested(listener: () => void): Promise<() => void>;
  onMenuRequested(listener: () => void): Promise<() => void>;
  onNewChatRequested(listener: () => void): Promise<() => void>;
  setExpanded(expanded: boolean): Promise<void>;
  setInputLayout(layout: PetInputLayout, compactWidth?: number): Promise<void>;
  setInputInteractive(interactive: boolean): Promise<void>;
  setInputOpenIntent(open: boolean): Promise<number>;
  requestInputFocus(): Promise<void>;
  beginInputPresentationSession(): Promise<PetInputPresentationCommit>;
  applyInputPresentation(
    input: PetInputPresentationApply,
  ): Promise<PetInputPresentationCommit>;
  resetPosition(expectedRevision: number): Promise<DesktopPreferences>;
  openMain(): Promise<void>;
  openCompanion(): Promise<void>;
  openSettings(): Promise<void>;
  exit(): Promise<void>;
}

export type PetInputLayout = "hidden" | "core" | "compact" | "expanded";

export interface PetInputPresentationApply {
  session_id: number;
  revision: number;
  layout: PetInputLayout;
  compact_width?: number;
  compact_height?: number;
  expanded_content_height?: number;
  interactive: boolean;
  request_focus: boolean;
}

export interface PetInputPresentationCommit {
  session_id: number;
  revision: number;
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
    async onInputRequested(listener) {
      return listen("presence-input-requested", listener);
    },
    async onInputToggleRequested(listener) {
      return listen("presence-input-toggle-requested", listener);
    },
    async onInputCloseRequested(listener) {
      return listen("presence-input-close-requested", listener);
    },
    async onMenuRequested(listener) {
      return listen("presence-menu-requested", listener);
    },
    async onNewChatRequested(listener) {
      return listen("presence-new-chat-requested", listener);
    },
    setExpanded: (expanded) =>
      invoke("pet_input_set_layout", { layout: expanded ? "expanded" : "hidden" }),
    setInputLayout: (layout, compactWidth) =>
      invoke("pet_input_set_layout", { layout, compactWidth }),
    setInputInteractive: (interactive) =>
      invoke("pet_input_set_interactive", { interactive }),
    setInputOpenIntent: (open) =>
      invoke<number>("pet_input_set_open_intent", { open }),
    requestInputFocus: () => invoke("pet_input_request_focus"),
    beginInputPresentationSession: () =>
      invoke<PetInputPresentationCommit>("pet_input_presentation_begin"),
    applyInputPresentation: (input) =>
      invoke<PetInputPresentationCommit>("pet_input_presentation_apply", { input }),
    resetPosition: (expectedRevision) =>
      invoke("pet_window_group_reset_position", { expectedRevision }),
    openMain: () => invoke("open_main_window"),
    openCompanion: () => invoke("open_companion_window"),
    openSettings: () => invoke("open_settings_window"),
    exit: () => invoke("pet_exit"),
  };
}

function createBrowserPetHost(): PetHost {
  let preferences = browserPreferences();
  let presentationSession = 0;
  let presentationRevision = 0;
  let inputIntentRevision = 0;
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
        ...(input.pet_anchor === undefined ? {} : { pet_anchor: input.pet_anchor }),
      };
      for (const listener of listeners) listener(preferences);
      return preferences;
    },
    async onPreferences(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    async onInputRequested() {
      return () => undefined;
    },
    async onInputToggleRequested() {
      return () => undefined;
    },
    async onInputCloseRequested() {
      return () => undefined;
    },
    async onMenuRequested() {
      return () => undefined;
    },
    async onNewChatRequested() {
      return () => undefined;
    },
    async setExpanded() {},
    async setInputLayout() {},
    async setInputInteractive() {},
    async setInputOpenIntent() {
      inputIntentRevision += 1;
      return inputIntentRevision;
    },
    async requestInputFocus() {},
    async beginInputPresentationSession() {
      presentationSession += 1;
      presentationRevision = 0;
      return { session_id: presentationSession, revision: presentationRevision };
    },
    async applyInputPresentation(input) {
      if (input.session_id !== presentationSession || input.revision <= presentationRevision) {
        throw new Error("PET_INPUT_PRESENTATION_STALE_REVISION");
      }
      presentationRevision = input.revision;
      return { session_id: presentationSession, revision: presentationRevision };
    },
    async resetPosition() {
      preferences = {
        ...preferences,
        revision: preferences.revision + 1,
        pet_anchor: null,
      };
      return preferences;
    },
    async openMain() {},
    async openCompanion() {},
    async openSettings() {},
    async exit() {},
  };
}

function browserPreferences(): DesktopPreferences {
  return {
    schema_version: 3,
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
    analytics_enabled: false,
    realtime_beta_enabled: false,
    realtime_backend: "auto",
    realtime_cloud_provider: "glm_realtime_flash",
    realtime_allow_cloud_fallback: false,
    realtime_activity_profile: "auto",
    realtime_interaction_intensity: "standard",
    realtime_voice_output: "fairy_voice",
    realtime_game_audio_default: false,
    realtime_online_assistance_enabled: false,
    realtime_memory_enabled: true,
    realtime_presence_max_minutes: 240,
    realtime_cloud_daily_limit_minutes: 180,
    realtime_local_keep_warm_minutes: 10,
    trash_auto_purge_30_days: false,
    pet_enabled: true,
    pet_always_on_top: true,
    pet_muted: false,
    pet_size_percent: 100,
    pet_opacity_percent: 92,
    pet_motion_enabled: true,
    pet_particles_enabled: true,
    pet_hover_enabled: true,
    pet_hover_dwell_ms: 250,
    pet_do_not_disturb: false,
    ambient_dialogue_enabled: true,
    ambient_dialogue_voice_enabled: false,
    ambient_generated_dialogue_enabled: false,
    pet_remember_position: true,
    pet_renderer_mode: "auto",
    pet_optics_mode: "standard",
    pet_activation_style: "fluid_response",
    pet_target_fps: 60,
    pet_anchor: null,
    developer_mode: false,
  };
}
