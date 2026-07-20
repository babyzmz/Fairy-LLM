import type { PresenceInteractionSnapshot } from "../domain/interaction";
import type { FairyMotionSnapshot } from "../domain/motionState";
import type { PresenceWorkState } from "../domain/projection";
import type { FairyVisualState } from "./CompatibilityFairyCanvas";
import type { PresenceRendererMode } from "./rendererSupport";

export interface PresenceRenderSnapshot {
  interaction: PresenceInteractionSnapshot | null;
  motion: FairyMotionSnapshot;
  input_capsule_visible: boolean;
  input_capsule_width: number;
  work_state: PresenceWorkState;
  speaking: boolean;
  voice_level: number;
  sleeping: boolean;
  reduced_motion: boolean;
  reduced_transparency?: boolean;
  increased_contrast?: boolean;
  size_scale: number;
  opacity: number;
  particles_enabled: boolean;
  optics_mode: "standard" | "enhanced";
  idle_for_ms: number;
  target_frame_rate: 60 | 144 | 300;
  frame_rate_limit: 15 | 30 | 60 | 144 | 300;
}

export type PresenceRendererStatus =
  | "initializing"
  | "running"
  | "stopped"
  | "suspended"
  | "context_lost"
  | "fallback"
  | "failed"
  | "disposed";

export interface PresenceRendererHealth {
  mode: "native" | Exclude<PresenceRendererMode, "auto">;
  status: PresenceRendererStatus;
  error_code:
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
    | null;
}

export interface PresenceRenderer {
  start(): void | Promise<void>;
  stop(): void;
  resize(width: number, height: number, devicePixelRatio: number): void;
  setSnapshot(snapshot: PresenceRenderSnapshot): void;
  suspend(): void;
  dispose(): void;
}

export function visualStateForSnapshot(
  snapshot: PresenceRenderSnapshot,
): FairyVisualState {
  switch (snapshot.motion.state) {
    case "error": return "error";
    case "awaiting_confirmation": return "awaiting_confirmation";
    case "speaking": return "speaking";
    case "responding": return "streaming";
    case "thinking": return snapshot.motion.activity === "tool" ? "tool" : "analyzing";
    case "submitting": return "analyzing";
    case "notify": return "ready";
    case "input":
    case "options": return "listening";
    case "aware":
    case "forming":
    case "returning": return "hover";
    case "repositioning": return "dragging";
    case "suspended":
    case "sleeping": return "sleeping";
    case "idle": return "idle";
  }
}

export function rendererFrameInterval(snapshot: PresenceRenderSnapshot): number {
  if (snapshot.reduced_motion) return Number.POSITIVE_INFINITY;
  const activityRate = snapshot.idle_for_ms >= 15_000
    ? 30
    : snapshot.target_frame_rate;
  return 1_000 / Math.min(snapshot.frame_rate_limit, activityRate);
}
