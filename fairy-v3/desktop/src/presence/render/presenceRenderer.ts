import type { PresenceInteractionSnapshot } from "../domain/interaction";
import type { PresenceWorkState } from "../domain/projection";
import type { FairyVisualState } from "./CompatibilityFairyCanvas";
import type { PresenceRendererMode } from "./rendererSupport";

export interface PresenceRenderSnapshot {
  interaction: PresenceInteractionSnapshot | null;
  work_state: PresenceWorkState;
  speaking: boolean;
  voice_level: number;
  sleeping: boolean;
  reduced_motion: boolean;
  size_scale: number;
  opacity: number;
  particles_enabled: boolean;
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
  mode: Exclude<PresenceRendererMode, "auto">;
  status: PresenceRendererStatus;
  error_code:
    | "WEBGL2_UNAVAILABLE"
    | "WEBGL2_CONTEXT_ERROR"
    | "WEBGL_CONTEXT_LOST"
    | "SHADER_INITIALIZATION_FAILED"
    | "CANVAS2D_UNAVAILABLE"
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
  if (snapshot.speaking) return "speaking";
  if (snapshot.work_state !== "idle") return snapshot.work_state;
  if (
    snapshot.interaction?.phase === "input_reveal" ||
    snapshot.interaction?.phase === "interactive"
  ) {
    return "listening";
  }
  if (
    snapshot.interaction !== null &&
    snapshot.interaction.cursor.band !== "outside"
  ) {
    return "hover";
  }
  return snapshot.sleeping ? "sleeping" : "idle";
}

export function rendererFrameInterval(snapshot: PresenceRenderSnapshot): number {
  if (snapshot.reduced_motion) return Number.POSITIVE_INFINITY;
  const state = visualStateForSnapshot(snapshot);
  return state === "idle" || state === "sleeping" ? 1_000 / 30 : 1_000 / 60;
}
