import { z } from "zod";

import type { DesktopPreferences } from "../../settings/client";
import type { PresenceRendererMode } from "../render/rendererSupport";

const CHANNEL_NAME = "fairy.presence.render-settings.v1";

export interface PresenceRenderSettings {
  schema_version: 1;
  mode: PresenceRendererMode;
  size_scale: number;
  opacity: number;
  motion_enabled: boolean;
  particles_enabled: boolean;
}

export interface PresenceRenderSettingsChannel {
  publish(settings: PresenceRenderSettings): void;
  request(): void;
  onSettings(listener: (settings: PresenceRenderSettings) => void): () => void;
  onRequest(listener: () => void): () => void;
  close(): void;
}

interface BroadcastPort {
  onmessage: ((event: MessageEvent<unknown>) => void) | null;
  postMessage(message: unknown): void;
  close(): void;
}

type BroadcastFactory = (name: string) => BroadcastPort | null;

const settingsSchema = z.object({
  schema_version: z.literal(1),
  mode: z.enum(["auto", "liquid", "compatibility"]),
  size_scale: z.number().min(0.75).max(1.5),
  opacity: z.number().min(0.4).max(1),
  motion_enabled: z.boolean(),
  particles_enabled: z.boolean(),
}).strict();

const messageSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("render-settings.request") }).strict(),
  z.object({
    kind: z.literal("render-settings.snapshot"),
    settings: settingsSchema,
  }).strict(),
]);

export const DEFAULT_PRESENCE_RENDER_SETTINGS: PresenceRenderSettings = Object.freeze({
  schema_version: 1,
  mode: "auto",
  size_scale: 1,
  opacity: 0.92,
  motion_enabled: true,
  particles_enabled: true,
});

export function safeRenderSettingsFromPreferences(
  preferences: DesktopPreferences,
): PresenceRenderSettings {
  return settingsSchema.parse({
    schema_version: 1,
    mode: preferences.pet_renderer_mode,
    size_scale: preferences.pet_size_percent / 100,
    opacity: preferences.pet_opacity_percent / 100,
    motion_enabled: preferences.pet_motion_enabled,
    particles_enabled: preferences.pet_particles_enabled,
  });
}

export function createPresenceRenderSettingsChannel(
  factory: BroadcastFactory = defaultBroadcastFactory,
): PresenceRenderSettingsChannel {
  const settingsListeners = new Set<(settings: PresenceRenderSettings) => void>();
  const requestListeners = new Set<() => void>();
  const broadcast = factory(CHANNEL_NAME);

  if (broadcast !== null) {
    broadcast.onmessage = (event) => {
      const parsed = messageSchema.safeParse(event.data);
      if (!parsed.success) return;
      if (parsed.data.kind === "render-settings.request") {
        for (const listener of requestListeners) listener();
        return;
      }
      for (const listener of settingsListeners) listener(parsed.data.settings);
    };
  }

  return {
    publish(settings) {
      const parsed = settingsSchema.safeParse(settings);
      if (!parsed.success) return;
      broadcast?.postMessage({
        kind: "render-settings.snapshot",
        settings: parsed.data,
      });
    },
    request() {
      broadcast?.postMessage({ kind: "render-settings.request" });
    },
    onSettings(listener) {
      settingsListeners.add(listener);
      return () => settingsListeners.delete(listener);
    },
    onRequest(listener) {
      requestListeners.add(listener);
      return () => requestListeners.delete(listener);
    },
    close() {
      settingsListeners.clear();
      requestListeners.clear();
      broadcast?.close();
    },
  };
}

function defaultBroadcastFactory(name: string): BroadcastPort | null {
  if (typeof BroadcastChannel === "undefined") return null;
  return new BroadcastChannel(name);
}
