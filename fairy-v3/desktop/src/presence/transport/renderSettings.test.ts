import { describe, expect, it, vi } from "vitest";

import type { DesktopPreferences } from "../../settings/client";
import {
  createPresenceRenderSettingsChannel,
  loadNativePresenceRenderSettings,
  safeRenderSettingsFromPreferences,
} from "./renderSettings";

describe("presence render settings", () => {
  it("loads only the bounded native render settings contract", async () => {
    await expect(loadNativePresenceRenderSettings(async (command) => {
      expect(command).toBe("pet_render_settings_get");
      return {
        schema_version: 4,
        mode: "liquid",
        optics_mode: "enhanced",
        activation_style: "fluid_response",
        size_scale: 1.05,
        opacity: 1,
        motion_enabled: true,
        particles_enabled: true,
        target_frame_rate: 300,
      };
    })).resolves.toEqual({
      schema_version: 4,
      mode: "liquid",
      optics_mode: "enhanced",
      activation_style: "fluid_response",
      size_scale: 1.05,
      opacity: 1,
      motion_enabled: true,
      particles_enabled: true,
      target_frame_rate: 300,
    });
  });

  it("rejects an expanded native settings payload", async () => {
    await expect(loadNativePresenceRenderSettings(async () => ({
      schema_version: 4,
      mode: "liquid",
      optics_mode: "enhanced",
      activation_style: "fluid_response",
      size_scale: 1,
      opacity: 1,
      motion_enabled: true,
      particles_enabled: true,
      target_frame_rate: 300,
      developer_mode: true,
    }))).resolves.toBeNull();
  });

  it("projects only bounded renderer fields from desktop preferences", () => {
    const projected = safeRenderSettingsFromPreferences(preferences());
    expect(projected).toEqual({
      schema_version: 4,
      mode: "liquid",
      optics_mode: "standard",
      activation_style: "fluid_response",
      size_scale: 1.25,
      opacity: 0.84,
      motion_enabled: false,
      particles_enabled: true,
      target_frame_rate: 300,
    });
    expect(projected).not.toHaveProperty("selected_profile_id");
    expect(projected).not.toHaveProperty("pet_anchor");
  });

  it("rejects malformed messages and closes the isolated port", () => {
    const port = {
      onmessage: null as ((event: MessageEvent<unknown>) => void) | null,
      postMessage: vi.fn(),
      close: vi.fn(),
    };
    const channel = createPresenceRenderSettingsChannel(() => port);
    const listener = vi.fn();
    channel.onSettings(listener);
    port.onmessage?.({ data: {
      kind: "render-settings.snapshot",
      settings: { schema_version: 2, mode: "liquid", size_scale: 99 },
    } } as MessageEvent<unknown>);
    expect(listener).not.toHaveBeenCalled();

    channel.request();
    expect(port.postMessage).toHaveBeenCalledWith({ kind: "render-settings.request" });
    channel.close();
    expect(port.close).toHaveBeenCalledOnce();
  });
});

function preferences(): DesktopPreferences {
  return {
    schema_version: 3,
    revision: 7,
    language: "system",
    launch_at_startup: false,
    minimize_to_tray: true,
    theme: "system",
    reduced_motion: false,
    compact_density: false,
    selected_profile_id: "private-provider",
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
    pet_size_percent: 125,
    pet_opacity_percent: 84,
    pet_motion_enabled: false,
    pet_particles_enabled: true,
    pet_hover_enabled: true,
    pet_hover_dwell_ms: 250,
    pet_do_not_disturb: false,
    ambient_dialogue_enabled: true,
    ambient_dialogue_voice_enabled: false,
    ambient_generated_dialogue_enabled: false,
    pet_remember_position: true,
    pet_renderer_mode: "liquid",
    pet_optics_mode: "standard",
    pet_activation_style: "fluid_response",
    pet_target_fps: 300,
    pet_anchor: { monitor_id: "primary", x_ratio: 0.5, y_ratio: 0.5 },
    developer_mode: false,
  };
}
