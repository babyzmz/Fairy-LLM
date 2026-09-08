import { z } from "zod";

import type { EventEnvelope } from "../../core/contracts";

export type PresenceActivity =
  | "ambient"
  | "attending"
  | "working"
  | "ready"
  | "needs_attention";
export type PresenceDensity = "quiet" | "normal" | "busy";
export type PresenceWorkState =
  | "idle"
  | "analyzing"
  | "tool"
  | "streaming"
  | "awaiting_confirmation"
  | "ready"
  | "error";

export interface PresenceNotice {
  id: string;
  tone: "info" | "critical";
  text: string;
}

export interface PresenceReply {
  id: string;
  text: string;
  kind: "scratch" | "task_notice";
  streaming: boolean;
}

export interface PresenceAmbientDialogue {
  presentation_id: string;
  dialogue_id: string;
  text: string;
  source: "protected" | "authored_original" | "generated_original";
  trigger:
    | "startup"
    | "idle_short"
    | "idle_long"
    | "user_returned"
    | "network_restored"
    | "battery_low"
    | "charging_started"
    | "self_commentary";
  locale: string;
  tts_allowed: boolean;
  expires_at: string;
  persona_digest: string;
}

export interface PresenceProjectionState {
  activity: PresenceActivity;
  work_state: PresenceWorkState;
  status_text: string;
  last_cursor: number;
  last_event_id: string | null;
  updated_at_ms: number;
  recent_activity_ms: number[];
  notice: PresenceNotice | null;
  reply: PresenceReply | null;
  ambient_dialogue: PresenceAmbientDialogue | null;
  speaking: boolean;
}

export interface PresenceView extends PresenceProjectionState {
  density: PresenceDensity;
}

const PRESENCE_STATUS_TEXTS = [
  "Standing by",
  "Reviewing your request",
  "Choosing the best model",
  "Planning the next step",
  "Preparing the next step",
  "Checking the result",
  "Preparing the result",
  "Generating media",
  "Writing the reply",
  "Resuming interrupted work",
  "Working in the project",
  "Preparing the preview",
  "Preview ready",
  "Waiting for your decision",
  "System action complete",
  "Ready for review",
  "Needs attention",
  "Fairy is preparing",
  "Fairy is loading the local model",
  "Fairy is connecting",
  "Fairy is listening",
  "Fairy is observing",
  "Fairy is thinking",
  "Fairy is searching",
  "Fairy is speaking",
  "Fairy is standing by",
  "Realtime privacy pause is active",
  "Realtime resources are limited",
  "Realtime companion needs attention",
] as const;
const PRESENCE_NOTICE_TEXTS = [
  "Preview is ready",
  "An approval needs your decision",
  "Fairy needs your attention",
  "Preview could not be prepared",
  "Media is ready",
  "Media generation could not be completed",
  "The action was not run",
] as const;

export const presenceProjectionStateSchema = z
  .object({
    activity: z.enum([
      "ambient",
      "attending",
      "working",
      "ready",
      "needs_attention",
    ]),
    work_state: z.enum([
      "idle",
      "analyzing",
      "tool",
      "streaming",
      "awaiting_confirmation",
      "ready",
      "error",
    ]),
    status_text: z.enum(PRESENCE_STATUS_TEXTS),
    last_cursor: z.number().int().nonnegative(),
    last_event_id: z.string().max(128).nullable(),
    updated_at_ms: z.number().int().nonnegative(),
    recent_activity_ms: z.array(z.number().int().nonnegative()).max(12),
    notice: z
      .object({
        id: z.string().min(1).max(160),
        tone: z.enum(["info", "critical"]),
        text: z.enum(PRESENCE_NOTICE_TEXTS),
      })
      .strict()
      .nullable(),
    reply: z
      .object({
        id: z.string().min(1).max(160),
        text: z.string().min(1).max(1_200),
        kind: z.enum(["scratch", "task_notice"]),
        streaming: z.boolean(),
      })
      .strict()
      .nullable(),
    ambient_dialogue: z
      .object({
        presentation_id: z.string().uuid(),
        dialogue_id: z.string().min(1).max(128),
        text: z.string().min(1).max(1_200),
        source: z.enum(["protected", "authored_original", "generated_original"]),
        trigger: z.enum([
          "startup",
          "idle_short",
          "idle_long",
          "user_returned",
          "network_restored",
          "battery_low",
          "charging_started",
          "self_commentary",
        ]),
        locale: z.string().min(2).max(16),
        tts_allowed: z.boolean(),
        expires_at: z.string().datetime({ offset: true }),
        persona_digest: z.string().regex(/^[a-f0-9]{64}$/u),
      })
      .strict()
      .nullable(),
    speaking: z.boolean(),
  })
  .strict()
  .refine((value) => value.notice === null || value.reply === null, {
    message: "Presence projection cannot contain a notice and reply together",
  });

const AFK_AFTER_MS = 5 * 60_000;
const DENSITY_WINDOW_MS = 30_000;

interface ProjectionRule {
  activity: PresenceActivity;
  workState: PresenceWorkState;
  statusText: string;
  notice?: Omit<PresenceNotice, "id">;
  replyText?: string;
}

const RULES: Readonly<Record<string, ProjectionRule>> = Object.freeze({
  "assistant.turn.started": {
    activity: "attending",
    workState: "analyzing",
    statusText: "Reviewing your request",
  },
  "assistant.route.selected": {
    activity: "attending",
    workState: "analyzing",
    statusText: "Choosing the best model",
  },
  "turn.trace.started": {
    activity: "attending",
    workState: "analyzing",
    statusText: "Planning the next step",
  },
  "command.created": {
    activity: "attending",
    workState: "analyzing",
    statusText: "Preparing the next step",
  },
  "command.queued": {
    activity: "attending",
    workState: "analyzing",
    statusText: "Preparing the next step",
  },
  "command.reclaimed": {
    activity: "attending",
    workState: "analyzing",
    statusText: "Resuming interrupted work",
  },
  "command.running": {
    activity: "working",
    workState: "tool",
    statusText: "Working in the project",
  },
  "command.succeeded": {
    activity: "attending",
    workState: "ready",
    statusText: "Checking the result",
  },
  "preview.starting": {
    activity: "working",
    workState: "tool",
    statusText: "Preparing the preview",
  },
  "preview.ready": {
    activity: "ready",
    workState: "ready",
    statusText: "Preview ready",
    notice: { tone: "info", text: "Preview is ready" },
  },
  "approval.requested": {
    activity: "needs_attention",
    workState: "awaiting_confirmation",
    statusText: "Waiting for your decision",
    notice: { tone: "critical", text: "An approval needs your decision" },
  },
  "assistant.budget.approval_requested": {
    activity: "needs_attention",
    workState: "awaiting_confirmation",
    statusText: "Waiting for your decision",
    notice: { tone: "critical", text: "An approval needs your decision" },
  },
  "command.waiting_approval": {
    activity: "needs_attention",
    workState: "awaiting_confirmation",
    statusText: "Waiting for your decision",
    notice: { tone: "critical", text: "An approval needs your decision" },
  },
  "approval.decided": {
    activity: "attending",
    workState: "analyzing",
    statusText: "Preparing the next step",
  },
  "system.action.completed": {
    activity: "ready",
    workState: "ready",
    statusText: "System action complete",
  },
  "assistant.message.delta": {
    activity: "working",
    workState: "streaming",
    statusText: "Writing the reply",
  },
  "assistant.turn.completed": {
    activity: "ready",
    workState: "ready",
    statusText: "Ready for review",
    replyText: "The task update is ready",
  },
  "assistant.turn.cancelled": {
    activity: "ambient",
    workState: "idle",
    statusText: "Standing by",
  },
  "preview.stopped": {
    activity: "ambient",
    workState: "idle",
    statusText: "Standing by",
  },
  "assistant.turn.failed": {
    activity: "needs_attention",
    workState: "error",
    statusText: "Needs attention",
    notice: { tone: "critical", text: "Fairy needs your attention" },
  },
  "command.failure": {
    activity: "needs_attention",
    workState: "error",
    statusText: "Needs attention",
    notice: { tone: "critical", text: "Fairy needs your attention" },
  },
  "command.failed": {
    activity: "needs_attention",
    workState: "error",
    statusText: "Needs attention",
    notice: { tone: "critical", text: "Fairy needs your attention" },
  },
  "command.interrupted": {
    activity: "needs_attention",
    workState: "error",
    statusText: "Needs attention",
    notice: { tone: "critical", text: "Fairy needs your attention" },
  },
  "command.rejected": {
    activity: "ambient",
    workState: "idle",
    statusText: "Standing by",
    notice: { tone: "info", text: "The action was not run" },
  },
  "command.cancelled": {
    activity: "ambient",
    workState: "idle",
    statusText: "Standing by",
  },
  "preview.failed": {
    activity: "needs_attention",
    workState: "error",
    statusText: "Needs attention",
    notice: { tone: "critical", text: "Preview could not be prepared" },
  },
  "media.generation.started": {
    activity: "working",
    workState: "tool",
    statusText: "Generating media",
  },
  "media.generation.progress": {
    activity: "working",
    workState: "tool",
    statusText: "Generating media",
  },
  "media.generation.completed": {
    activity: "ready",
    workState: "ready",
    statusText: "Preparing the result",
    notice: { tone: "info", text: "Media is ready" },
  },
  "media.generation.cancelled": {
    activity: "ambient",
    workState: "idle",
    statusText: "Standing by",
  },
  "media.generation.failed": {
    activity: "needs_attention",
    workState: "error",
    statusText: "Needs attention",
    notice: { tone: "critical", text: "Media generation could not be completed" },
  },
});

const traceStepPayloadSchema = z.object({
  kind: z.enum([
    "route",
    "plan",
    "reasoning",
    "model",
    "tool",
    "approval",
    "observation",
    "verification",
    "artifact",
    "response",
    "voice",
  ]),
  status: z.enum([
    "pending",
    "running",
    "waiting",
    "succeeded",
    "failed",
    "cancelled",
    "skipped",
  ]),
}).passthrough();

function initialPresenceProjection(): PresenceProjectionState {
  return {
    activity: "ambient",
    work_state: "idle",
    status_text: "Standing by",
    last_cursor: 0,
    last_event_id: null,
    updated_at_ms: 0,
    recent_activity_ms: [],
    notice: null,
    reply: null,
    ambient_dialogue: null,
    speaking: false,
  };
}

function reducePresenceProjection(
  state: PresenceProjectionState,
  event: EventEnvelope,
): PresenceProjectionState {
  if (event.visibility !== "user" || event.cursor <= state.last_cursor) return state;
  const rule = RULES[event.event_type] ?? traceProjectionRule(event);
  if (rule === undefined) return state;
  const occurredAt = Date.parse(event.created_at);
  if (!Number.isFinite(occurredAt) || occurredAt < 0) return state;

  const eventKey = `event:${event.id}`;
  const recentActivity = [...state.recent_activity_ms, occurredAt]
    .filter((value) => value >= occurredAt - DENSITY_WINDOW_MS)
    .slice(-12);
  return {
    activity: rule.activity,
    work_state: rule.workState,
    status_text: rule.statusText,
    last_cursor: event.cursor,
    last_event_id: event.id,
    updated_at_ms: occurredAt,
    recent_activity_ms: recentActivity,
    notice:
      rule.notice === undefined ? null : { id: eventKey, ...rule.notice },
    reply:
      rule.replyText === undefined
        ? null
        : {
            id: eventKey,
            text: rule.replyText,
            kind: "task_notice",
            streaming: false,
          },
    ambient_dialogue: null,
    speaking: false,
  };
}

function traceProjectionRule(event: EventEnvelope): ProjectionRule | undefined {
  if (!event.event_type.startsWith("turn.trace.step.")) return undefined;
  const parsed = traceStepPayloadSchema.safeParse(event.payload);
  if (!parsed.success) return undefined;
  const { kind, status } = parsed.data;
  if (status === "failed") {
    return {
      activity: "needs_attention",
      workState: "error",
      statusText: "Needs attention",
      notice: { tone: "critical", text: "Fairy needs your attention" },
    };
  }
  if (status === "cancelled") {
    return { activity: "ambient", workState: "idle", statusText: "Standing by" };
  }
  if (kind === "approval" && status === "waiting") {
    return {
      activity: "needs_attention",
      workState: "awaiting_confirmation",
      statusText: "Waiting for your decision",
      notice: { tone: "critical", text: "An approval needs your decision" },
    };
  }
  if (kind === "tool" || kind === "artifact") {
    return status === "succeeded"
      ? { activity: "attending", workState: "analyzing", statusText: "Checking the result" }
      : { activity: "working", workState: "tool", statusText: "Working in the project" };
  }
  if (kind === "response") {
    return { activity: "working", workState: "streaming", statusText: "Writing the reply" };
  }
  if (kind === "voice") {
    return { activity: "ready", workState: "ready", statusText: "Ready for review" };
  }
  if (kind === "observation" || kind === "verification") {
    return { activity: "attending", workState: "analyzing", statusText: "Checking the result" };
  }
  return {
    activity: "attending",
    workState: "analyzing",
    statusText: kind === "plan" ? "Planning the next step" : "Preparing the next step",
  };
}

export const PresenceProjection = Object.freeze({
  initial: initialPresenceProjection,
  reduce: reducePresenceProjection,
});

interface PresenceViewOptions {
  now_ms: number;
  quiet_mode: boolean;
  dismissed_notice_ids: readonly string[];
}

export function derivePresenceView(
  state: PresenceProjectionState,
  options: PresenceViewOptions,
): PresenceView {
  const afkEligible = state.work_state === "idle" || state.work_state === "ready";
  const afk = afkEligible && (
    state.updated_at_ms === 0 || options.now_ms - state.updated_at_ms >= AFK_AFTER_MS
  );
  const activeInWindow = state.recent_activity_ms.filter(
    (value) => value >= options.now_ms - DENSITY_WINDOW_MS,
  ).length;
  const density: PresenceDensity = afk
    ? "quiet"
    : activeInWindow >= 4
      ? "busy"
      : "normal";
  const notice =
    state.notice !== null &&
    !options.dismissed_notice_ids.includes(state.notice.id) &&
    (!options.quiet_mode || state.notice.tone === "critical")
      ? state.notice
      : null;

  if (afk) {
    return {
      ...state,
      activity: "ambient",
      work_state: "idle",
      status_text: "Standing by",
      density,
      notice,
      reply: null,
    };
  }
  return {
    ...state,
    density,
    notice,
    reply: options.quiet_mode ? null : state.reply,
  };
}
