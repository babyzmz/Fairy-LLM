import { z } from "zod";

import type { PresenceInteractionSnapshot } from "./interaction";
import type { PresenceWorkState } from "./projection";

export const fairyStateSchema = z.enum([
  "idle",
  "aware",
  "forming",
  "input",
  "options",
  "submitting",
  "thinking",
  "responding",
  "speaking",
  "notify",
  "awaiting_confirmation",
  "error",
  "returning",
  "suspended",
  "repositioning",
  "sleeping",
]);

export const fairySurfaceSchema = z.enum([
  "core",
  "input",
  "options",
  "submission",
  "reply",
  "notice",
]);

export const fairyActivitySchema = z.enum([
  "none",
  "model",
  "tool",
  "approval",
  "response",
  "voice",
  "notification",
]);

export const fairyMotionSnapshotSchema = z.object({
  schema_version: z.literal(1),
  revision: z.number().int().nonnegative(),
  state: fairyStateSchema,
  surface: fairySurfaceSchema,
  activity: fairyActivitySchema,
  state_started_at_ms: z.number().int().nonnegative(),
  state_duration_ms: z.number().int().positive().nullable(),
  phase_progress: z.number().min(0).max(1),
  content_visible: z.boolean(),
  surface_interactive: z.boolean(),
  capsule_visible: z.boolean(),
  reduced_motion: z.boolean(),
  do_not_disturb: z.boolean(),
}).strict();

export type FairyState = z.infer<typeof fairyStateSchema>;
export type FairySurface = z.infer<typeof fairySurfaceSchema>;
export type FairyActivity = z.infer<typeof fairyActivitySchema>;
export type FairyMotionSnapshot = z.infer<typeof fairyMotionSnapshotSchema>;

export type FairySubmissionPhase =
  | "sending"
  | "accepted"
  | "cancelling"
  | "cancelled"
  | "failed";

export interface FairyMotionFacts {
  interaction: PresenceInteractionSnapshot | null;
  input_window_visible: boolean;
  input_content_visible: boolean;
  input_interactive: boolean;
  manual_input_open: boolean;
  menu_open: boolean;
  reply: { streaming: boolean } | null;
  notice_tone: "info" | "critical" | null;
  submission_phase: FairySubmissionPhase | null;
  work_state: PresenceWorkState;
  speaking: boolean;
  moving: boolean;
  sleeping: boolean;
  reduced_motion: boolean;
  do_not_disturb: boolean;
}

export const DEFAULT_FAIRY_MOTION_SNAPSHOT: FairyMotionSnapshot = Object.freeze({
  schema_version: 1,
  revision: 0,
  state: "idle",
  surface: "core",
  activity: "none",
  state_started_at_ms: 0,
  state_duration_ms: null,
  phase_progress: 1,
  content_visible: false,
  surface_interactive: true,
  capsule_visible: false,
  reduced_motion: false,
  do_not_disturb: false,
});

interface FairyMotionTarget {
  state: FairyState;
  surface: FairySurface;
  activity: FairyActivity;
  content_visible: boolean;
  surface_interactive: boolean;
  capsule_visible: boolean;
  reduced_motion: boolean;
  do_not_disturb: boolean;
}

export function advanceFairyMotionSnapshot(
  previous: FairyMotionSnapshot,
  facts: FairyMotionFacts,
  nowMs: number,
): FairyMotionSnapshot {
  const now = Math.max(0, Math.trunc(Number.isFinite(nowMs) ? nowMs : 0));
  const target = resolveFairyMotionTarget(facts);
  const identityChanged =
    previous.state !== target.state ||
    previous.surface !== target.surface ||
    previous.activity !== target.activity ||
    previous.content_visible !== target.content_visible ||
    previous.surface_interactive !== target.surface_interactive ||
    previous.capsule_visible !== target.capsule_visible ||
    previous.reduced_motion !== target.reduced_motion ||
    previous.do_not_disturb !== target.do_not_disturb;
  const stateStartedAt = previous.state === target.state
    ? Math.min(previous.state_started_at_ms, now)
    : now;
  const duration = stateDuration(target.state, target.reduced_motion);
  const phaseProgress = duration === null
    ? 1
    : clamp((now - stateStartedAt) / duration, 0, 1);

  return {
    schema_version: 1,
    revision: identityChanged ? previous.revision + 1 : previous.revision,
    ...target,
    state_started_at_ms: stateStartedAt,
    state_duration_ms: duration,
    phase_progress: phaseProgress,
  };
}

function resolveFairyMotionTarget(facts: FairyMotionFacts): FairyMotionTarget {
  const state = resolveState(facts);
  const surface = resolveSurface(facts, state);
  return {
    state,
    surface,
    activity: resolveActivity(facts, state),
    content_visible: surface !== "core" && (
      surface !== "input" || facts.manual_input_open || facts.input_content_visible
    ),
    surface_interactive:
      surface === "core" ||
      surface !== "input" ||
      facts.manual_input_open ||
      facts.input_interactive,
    capsule_visible: surface !== "core",
    reduced_motion: facts.reduced_motion,
    do_not_disturb: facts.do_not_disturb,
  };
}

function resolveSurface(facts: FairyMotionFacts, state: FairyState): FairySurface {
  if (state === "error" || state === "awaiting_confirmation") {
    if (facts.notice_tone !== null) return "notice";
    if (facts.submission_phase !== null) return "submission";
    return "notice";
  }
  if ((state === "speaking" || state === "responding") && facts.reply !== null) {
    return "reply";
  }
  if ((state === "thinking" || state === "submitting") && facts.submission_phase !== null) {
    return "submission";
  }
  if (state === "notify") {
    if (facts.notice_tone !== null) return "notice";
    if (facts.submission_phase !== null) return "submission";
  }
  if (["thinking", "responding", "speaking"].includes(state)) return "core";
  if (facts.notice_tone !== null) return "notice";
  if (facts.reply !== null) return "reply";
  if (facts.submission_phase !== null) return "submission";
  if (facts.menu_open) return "options";
  if (facts.manual_input_open || facts.input_window_visible) return "input";
  return "core";
}

function resolveState(facts: FairyMotionFacts): FairyState {
  if (facts.moving || facts.interaction?.phase === "repositioning") {
    return "repositioning";
  }
  if (facts.interaction?.phase === "suspended") return "suspended";
  if (facts.work_state === "error" || facts.submission_phase === "failed") {
    return "error";
  }
  if (facts.work_state === "awaiting_confirmation") {
    return "awaiting_confirmation";
  }
  if (facts.speaking) return "speaking";
  if (facts.reply?.streaming || facts.work_state === "streaming") {
    return "responding";
  }
  if (facts.work_state === "analyzing" || facts.work_state === "tool") {
    return "thinking";
  }
  if (
    facts.submission_phase === "sending" ||
    facts.submission_phase === "accepted" ||
    facts.submission_phase === "cancelling"
  ) {
    return "submitting";
  }
  if (facts.menu_open) return "options";
  if (facts.reply !== null) return "responding";
  if (facts.notice_tone !== null || facts.submission_phase === "cancelled") {
    return "notify";
  }
  if (facts.manual_input_open) return "input";

  switch (facts.interaction?.phase) {
    case "aware": return "aware";
    case "droplet":
    case "stretching":
    case "input_reveal": return "forming";
    case "interactive": return "input";
    case "returning": return "returning";
    case "idle":
    case undefined: return facts.sleeping ? "sleeping" : "idle";
  }
}

function resolveActivity(
  facts: FairyMotionFacts,
  state: FairyState,
): FairyActivity {
  if (state === "awaiting_confirmation") return "approval";
  if (state === "speaking") return "voice";
  if (state === "responding") return "response";
  if (facts.work_state === "tool") return "tool";
  if (state === "thinking" || state === "submitting") return "model";
  if (state === "notify") return "notification";
  return "none";
}

function stateDuration(state: FairyState, reducedMotion: boolean): number | null {
  switch (state) {
    case "aware": return reducedMotion ? 120 : 80;
    case "forming": return reducedMotion ? 200 : 420;
    case "submitting": return reducedMotion ? 180 : 320;
    case "notify": return 600;
    case "returning": return reducedMotion ? 200 : 400;
    default: return null;
  }
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}
