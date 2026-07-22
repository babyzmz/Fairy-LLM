import type { PresenceInteractionSnapshot } from "../domain/interaction";
import type { FairyMotionSnapshot } from "../domain/motionState";
import type { PresenceWorkState } from "../domain/projection";
import type { FairyVisualState } from "./CompatibilityFairyCanvas";
import type { PresenceRendererHealth } from "../transport/rendererHealth";

export type { PresenceRendererHealth } from "../transport/rendererHealth";

export interface PresenceRenderSnapshot {
  interaction: PresenceInteractionSnapshot | null;
  motion: FairyMotionSnapshot;
  input_capsule_visible: boolean;
  input_capsule_width: number;
  input_capsule_height: number;
  input_surface_visible: boolean;
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
  activation_style?: "classic" | "fluid_response";
  idle_for_ms: number;
  target_frame_rate: 60 | 144 | 300;
  frame_rate_limit: 15 | 30 | 60 | 144 | 300;
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
  return 1_000 / rendererFrameRate(snapshot);
}

export function rendererFrameRate(snapshot: PresenceRenderSnapshot): number {
  const activityRate = snapshot.idle_for_ms >= 15_000
    ? 30
    : hasActiveForegroundAnimation(snapshot)
      ? snapshot.target_frame_rate
      : Math.min(snapshot.target_frame_rate, 60);
  const sleepingRate = ["sleeping", "suspended"].includes(snapshot.motion.state)
    ? 15
    : activityRate;
  return Math.min(snapshot.frame_rate_limit, sleepingRate);
}

export function hasActiveForegroundAnimation(snapshot: PresenceRenderSnapshot): boolean {
  if (snapshot.interaction?.phase === "repositioning") return true;
  if (snapshot.input_capsule_visible || snapshot.input_surface_visible) return true;
  return !["idle", "sleeping", "suspended"].includes(snapshot.motion.state);
}
