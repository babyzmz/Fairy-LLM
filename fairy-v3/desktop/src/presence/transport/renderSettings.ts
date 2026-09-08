import { invoke, isTauri } from "@tauri-apps/api/core";
import { z } from "zod";
import type { DesktopPreferences, PetForm } from "../../settings/client";
import type { PresenceRendererMode } from "../render/rendererSupport";

const CHANNEL_NAME = "fairy.presence.render-settings.v1";
/** Legacy v4 input stays accepted; native and published output is canonical v5. */
export interface PresenceRenderSettings {
  schema_version: 4 | 5;
  form?: PetForm;
  mode: PresenceRendererMode;
  optics_mode: "standard" | "enhanced";
  activation_style: "classic" | "fluid_response";
  size_scale: number;
  opacity: number;
  motion_enabled: boolean;
  particles_enabled: boolean;
  target_frame_rate: 60 | 144 | 300;
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
type SettingsInvoke = (command: string) => Promise<unknown>;
const commonFields = {
  mode: z.enum(["auto", "liquid", "compatibility"]),
  optics_mode: z.enum(["standard", "enhanced"]),
  activation_style: z.enum(["classic", "fluid_response"]),
  size_scale: z.number().min(0.75).max(1.5),
  opacity: z.number().min(0.4).max(1),
  motion_enabled: z.boolean(),
  particles_enabled: z.boolean(),
  target_frame_rate: z.union([z.literal(60), z.literal(144), z.literal(300)]),
};
const legacySettingsSchema = z.object({schema_version:z.literal(4), ...commonFields}).strict();
const v5SettingsSchema = z.object({
  schema_version:z.literal(5), form:z.enum(["liquid_glass","hdd_eye"]), ...commonFields,
}).strict();
const settingsSchema = z.union([v5SettingsSchema,legacySettingsSchema]).transform(value=>({
  ...value, schema_version:5 as const,
  form:"form" in value ? value.form : "liquid_glass" as const,
}));
const messageSchema = z.discriminatedUnion("kind", [
  z.object({kind:z.literal("render-settings.request")}).strict(),
  z.object({kind:z.literal("render-settings.snapshot"),settings:settingsSchema}).strict(),
]);
export const DEFAULT_PRESENCE_RENDER_SETTINGS: PresenceRenderSettings = Object.freeze({
  schema_version:5, form:"liquid_glass", mode:"auto", optics_mode:"standard",
  activation_style:"fluid_response", size_scale:1, opacity:.92,
  motion_enabled:true, particles_enabled:true, target_frame_rate:60,
});
export function safeRenderSettingsFromPreferences(preferences: DesktopPreferences): PresenceRenderSettings {
  return settingsSchema.parse({
    schema_version:5, form:preferences.pet_form ?? "liquid_glass",
    mode:preferences.pet_renderer_mode, optics_mode:preferences.pet_optics_mode,
    activation_style:preferences.pet_activation_style,
    size_scale:preferences.pet_size_percent/100, opacity:preferences.pet_opacity_percent/100,
    motion_enabled:preferences.pet_motion_enabled, particles_enabled:preferences.pet_particles_enabled,
    target_frame_rate:preferences.pet_target_fps,
  });
}
export async function loadNativePresenceRenderSettings(
  invokeCommand: SettingsInvoke | null = isTauri() ? command=>invoke(command) : null,
): Promise<PresenceRenderSettings | null> {
  if (invokeCommand===null) return null;
  try { return settingsSchema.parse(await invokeCommand("pet_render_settings_get")); }
  catch { return null; }
}
export function createPresenceRenderSettingsChannel(
  factory: BroadcastFactory = defaultBroadcastFactory,
): PresenceRenderSettingsChannel {
  const settingsListeners=new Set<(settings:PresenceRenderSettings)=>void>();
  const requestListeners=new Set<()=>void>();
  const broadcast=factory(CHANNEL_NAME);
  let closed=false;
  if (broadcast!==null) broadcast.onmessage=event=>{
    if (closed) return;
    const parsed=messageSchema.safeParse(event.data);
    if (!parsed.success) return;
    if (parsed.data.kind==="render-settings.request") {
      for (const listener of requestListeners) listener();
    } else {
      for (const listener of settingsListeners) listener(parsed.data.settings);
    }
  };
  return {
    publish(settings) {
      if (closed) return;
      const parsed=settingsSchema.safeParse(settings);
      if (parsed.success) broadcast?.postMessage({kind:"render-settings.snapshot",settings:parsed.data});
    },
    request() { if (!closed) broadcast?.postMessage({kind:"render-settings.request"}); },
    onSettings(listener) { if (!closed) settingsListeners.add(listener); return ()=>{settingsListeners.delete(listener);}; },
    onRequest(listener) { if (!closed) requestListeners.add(listener); return ()=>{requestListeners.delete(listener);}; },
    close() { if (closed) return; closed=true; settingsListeners.clear(); requestListeners.clear();
      if (broadcast) { broadcast.onmessage=null; broadcast.close(); }
    },
  };
}
function defaultBroadcastFactory(name: string): BroadcastPort | null {
  return typeof BroadcastChannel==="undefined" ? null : new BroadcastChannel(name);
}
