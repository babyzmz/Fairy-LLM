import { invoke, isTauri } from "@tauri-apps/api/core";
import { z } from "zod";

import type { PresenceRendererHealth } from "../render/presenceRenderer";

const healthReportSchema = z.object({
  schema_version: z.literal(2),
  requested_mode: z.enum(["auto", "liquid", "compatibility"]),
  mode: z.enum(["native", "liquid", "compatibility"]),
  actual_backend: z.enum([
    "none",
    "native_liquid_glass",
    "webgl_compatibility",
    "canvas_compatibility",
  ]),
  optics_source: z.enum([
    "none",
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
  ]).nullable(),
  monitor_refresh_hz: z.number().int().nonnegative().max(1_000),
  effective_fps: z.number().int().nonnegative().max(1_000),
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
        schema_version: 2,
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
