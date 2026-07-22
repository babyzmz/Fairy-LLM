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
  | "NATIVE_GPU_STOP_FAILED"
  | "NATIVE_DDA_UNAVAILABLE";

export type PresenceActualRendererBackend =
  | "none"
  | "native_liquid_glass"
  | "native_identity_fallback"
  | "webgl_compatibility"
  | "canvas_compatibility";

export type PresenceOpticsSource =
  | "none"
  | "desktop_duplication"
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
  dda_exclusion: "not_requested" | "applied" | "failed" | "unsupported";
  source_format: "bgra8" | "rgb10a2" | "rgba16f" | null;
  adapter_luid: string | null;
  source_frame_age_ms: number | null;
  capture_to_present_p95_ms: number;
  access_lost_count: number;
  monitor_handoff: "idle" | "preparing" | "ready" | "failed";
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
  dda_exclusion: "not_requested",
  source_format: null,
  adapter_luid: null,
  source_frame_age_ms: null,
  capture_to_present_p95_ms: 0,
  access_lost_count: 0,
  monitor_handoff: "idle",
});
