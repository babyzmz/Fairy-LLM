import type { PresenceRendererMode } from "../render/rendererSupport";

export type PresenceRendererStatus =
  | "initializing"
  | "running"
  | "stopped"
  | "suspended"
  | "context_lost"
  | "fallback"
  | "failed"
  | "disposed";

export type PresenceRendererErrorCode =
  | "WEBGL2_UNAVAILABLE"
  | "WEBGL2_CONTEXT_ERROR"
  | "WEBGL_CONTEXT_LOST"
  | "SHADER_INITIALIZATION_FAILED"
  | "CANVAS2D_UNAVAILABLE"
  | "NATIVE_GPU_UNAVAILABLE"
  | "NATIVE_GPU_START_FAILED"
  | "NATIVE_GPU_UPDATE_FAILED"
  | "NATIVE_GPU_RUNTIME_FAILED"
  | "NATIVE_GPU_STOP_FAILED";

export type PresenceActualRendererBackend =
  | "none"
  | "native_liquid_glass"
  | "webgl_compatibility"
  | "canvas_compatibility";

export type PresenceOpticsSource =
  | "none"
  | "host_backdrop_identity"
  | "webgl_texture"
  | "procedural";

export interface PresenceRendererHealth {
  requested_mode: PresenceRendererMode;
  mode: "native" | Exclude<PresenceRendererMode, "auto">;
  actual_backend: PresenceActualRendererBackend;
  optics_source: PresenceOpticsSource;
  status: PresenceRendererStatus;
  error_code: PresenceRendererErrorCode | null;
  fallback_reason: PresenceRendererErrorCode | null;
  monitor_refresh_hz: number;
  effective_fps: number;
}

export const UNAVAILABLE_RENDERER_HEALTH: PresenceRendererHealth = Object.freeze({
  requested_mode: "auto",
  mode: "compatibility",
  actual_backend: "none",
  optics_source: "none",
  status: "stopped",
  error_code: null,
  fallback_reason: null,
  monitor_refresh_hz: 0,
  effective_fps: 0,
});
