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
  onMenuRequested(listener: () => void): Promise<() => void>;
  onNewChatRequested(listener: () => void): Promise<() => void>;
  setExpanded(expanded: boolean): Promise<void>;
  setInputLayout(layout: PetInputLayout, compactWidth?: number): Promise<void>;
  setInputInteractive(interactive: boolean): Promise<void>;
  requestInputFocus(): Promise<void>;
  beginInputPresentationSession(): Promise<PetInputPresentationCommit>;
  applyInputPresentation(
    input: PetInputPresentationApply,
  ): Promise<PetInputPresentationCommit>;
  beginGroupDrag(
    sessionId: string,
    initialDeltaX: number,
    initialDeltaY: number,
  ): Promise<void>;
  moveGroupDrag(sessionId: string, deltaX: number, deltaY: number): Promise<boolean>;
  endGroupDrag(sessionId: string, expectedRevision: number): Promise<DesktopPreferences>;
  resetPosition(expectedRevision: number): Promise<DesktopPreferences>;
  openMain(): Promise<void>;
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
    requestInputFocus: () => invoke("pet_input_request_focus"),
    beginInputPresentationSession: () =>
      invoke<PetInputPresentationCommit>("pet_input_presentation_begin"),
    applyInputPresentation: (input) =>
      invoke<PetInputPresentationCommit>("pet_input_presentation_apply", { input }),
    beginGroupDrag: (sessionId, initialDeltaX, initialDeltaY) =>
      invoke("pet_window_group_begin_drag", {
        sessionId,
        initialDeltaX: Math.round(initialDeltaX),
        initialDeltaY: Math.round(initialDeltaY),
      }),
    moveGroupDrag: (sessionId, deltaX, deltaY) =>
      invoke("pet_window_group_move", {
        sessionId,
        deltaX: Math.round(deltaX),
        deltaY: Math.round(deltaY),
      }),
    endGroupDrag: (sessionId, expectedRevision) =>
      invoke("pet_window_group_end_drag", { sessionId, expectedRevision }),
    resetPosition: (expectedRevision) =>
      invoke("pet_window_group_reset_position", { expectedRevision }),
    openMain: () => invoke("open_main_window"),
    openSettings: () => invoke("open_settings_window"),
    exit: () => invoke("pet_exit"),
  };
}

function createBrowserPetHost(): PetHost {
  let preferences = browserPreferences();
  let presentationSession = 0;
  let presentationRevision = 0;
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
    async onMenuRequested() {
      return () => undefined;
    },
    async onNewChatRequested() {
      return () => undefined;
    },
    async setExpanded() {},
    async setInputLayout() {},
    async setInputInteractive() {},
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
    async beginGroupDrag() {},
    async moveGroupDrag() {
      return true;
    },
    async endGroupDrag() {
      preferences = { ...preferences, revision: preferences.revision + 1 };
      return preferences;
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
    realtime_provider: "auto",
    realtime_voice_mode: "native",
    realtime_game_audio_default: false,
    realtime_memory_enabled: true,
    realtime_max_session_minutes: 30,
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
    pet_remember_position: true,
    pet_renderer_mode: "auto",
    pet_optics_mode: "standard",
    pet_target_fps: 60,
    pet_anchor: null,
    developer_mode: false,
  };
}
