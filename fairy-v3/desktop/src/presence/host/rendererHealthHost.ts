import { invoke, isTauri } from "@tauri-apps/api/core";
import { z } from "zod";

import type { PresenceRendererHealth } from "../render/presenceRenderer";

const healthReportSchema = z.object({
  schema_version: z.literal(3),
  requested_mode: z.enum(["auto", "liquid", "compatibility"]),
  mode: z.enum(["native", "liquid", "compatibility"]),
  actual_backend: z.enum([
    "none",
    "native_liquid_glass",
    "native_identity_fallback",
    "webgl_compatibility",
    "canvas_compatibility",
  ]),
  optics_source: z.enum([
    "none",
    "desktop_duplication",
    "host_backdrop_identity",
    "webgl_texture",
    "procedural",
  ]),
  status: z.enum([
    "initializing",
    "running",
    "stopped",
    "suspended",
    "context_lost",
    "fallback",
    "failed",
    "disposed",
  ]),
  error_code: z.enum([
    "WEBGL2_UNAVAILABLE",
    "WEBGL2_CONTEXT_ERROR",
    "WEBGL_CONTEXT_LOST",
    "SHADER_INITIALIZATION_FAILED",
    "CANVAS2D_UNAVAILABLE",
    "NATIVE_GPU_UNAVAILABLE",
    "NATIVE_GPU_START_FAILED",
    "NATIVE_GPU_UPDATE_FAILED",
    "NATIVE_GPU_RUNTIME_FAILED",
    "NATIVE_GPU_STOP_FAILED",
    "NATIVE_DDA_UNAVAILABLE",
  ]).nullable(),
  fallback_reason: z.enum([
    "WEBGL2_UNAVAILABLE",
    "WEBGL2_CONTEXT_ERROR",
    "WEBGL_CONTEXT_LOST",
    "SHADER_INITIALIZATION_FAILED",
    "CANVAS2D_UNAVAILABLE",
    "NATIVE_GPU_UNAVAILABLE",
    "NATIVE_GPU_START_FAILED",
    "NATIVE_GPU_UPDATE_FAILED",
    "NATIVE_GPU_RUNTIME_FAILED",
    "NATIVE_GPU_STOP_FAILED",
    "NATIVE_DDA_UNAVAILABLE",
  ]).nullable(),
  monitor_refresh_hz: z.number().int().nonnegative().max(1_000),
  effective_fps: z.number().int().nonnegative().max(1_000),
  dda_exclusion: z.enum(["not_requested", "applied", "failed", "unsupported"]),
  source_format: z.enum(["bgra8", "rgb10a2", "rgba16f"]).nullable(),
  adapter_luid: z.string().regex(/^[0-9A-F]{8}:[0-9A-F]{8}$/u).nullable(),
  source_frame_age_ms: z.number().nonnegative().nullable(),
  capture_to_present_p95_ms: z.number().nonnegative(),
  access_lost_count: z.number().int().nonnegative(),
  monitor_handoff: z.enum(["idle", "preparing", "ready", "failed"]),
}).strict();

const directiveSchema = z.enum([
  "continue",
  "force_compatibility",
  "disable_pet",
]);

export type PresenceRendererDirective = z.infer<typeof directiveSchema>;
export type RendererHealthInvoke = (
  command: string,
  args: Record<string, unknown>,
) => Promise<unknown>;

export interface PresenceRendererHealthHost {
  report(health: PresenceRendererHealth): Promise<PresenceRendererDirective>;
}

export function createPresenceRendererHealthHost(
  suppliedInvoke?: RendererHealthInvoke,
): PresenceRendererHealthHost {
  const invokeCommand = suppliedInvoke ?? (isTauri() ? invoke : null);
  return {
    async report(health) {
      const parsed = healthReportSchema.safeParse({
        schema_version: 3,
        ...health,
      });
      if (!parsed.success || invokeCommand === null) return "continue";
      try {
        const response = await invokeCommand("pet_renderer_report_health", {
          report: parsed.data,
        });
        const directive = directiveSchema.safeParse(response);
        return directive.success ? directive.data : "continue";
      } catch {
        return "continue";
      }
    },
  };
}
