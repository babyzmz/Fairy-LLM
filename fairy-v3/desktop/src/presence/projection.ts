import { z } from "zod";

import type { EventEnvelope } from "../core/contracts";

export type PresenceActivity =
  | "ambient"
  | "attending"
  | "working"
  | "ready"
  | "needs_attention";
export type PresenceDensity = "quiet" | "normal" | "busy";

export interface PresenceNotice {
  id: string;
  tone: "info" | "critical";
  text: string;
}

export interface PresenceReply {
  id: string;
  text: string;
}

export interface PresenceProjectionState {
  activity: PresenceActivity;
  status_text: string;
  last_cursor: number;
  last_event_id: string | null;
  updated_at_ms: number;
  recent_activity_ms: number[];
  notice: PresenceNotice | null;
  reply: PresenceReply | null;
}

export interface PresenceView extends PresenceProjectionState {
  density: PresenceDensity;
}

const PRESENCE_STATUS_TEXTS = [
  "Standing by",
  "Reviewing your request",
  "Preparing the next step",
  "Resuming interrupted work",
  "Working in the project",
  "Preparing the preview",
  "Preview ready",
  "Waiting for your decision",
  "System action complete",
  "Ready for review",
  "Needs attention",
] as const;
const PRESENCE_NOTICE_TEXTS = [
  "Preview is ready",
  "An approval needs your decision",
  "Fairy needs your attention",
  "Preview could not be prepared",
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
        text: z.literal("The task update is ready"),
      })
      .strict()
      .nullable(),
  })
  .strict()
  .refine((value) => value.notice === null || value.reply === null, {
    message: "Presence projection cannot contain a notice and reply together",
  });

const AFK_AFTER_MS = 5 * 60_000;
const DENSITY_WINDOW_MS = 30_000;

interface ProjectionRule {
  activity: PresenceActivity;
  statusText: string;
  notice?: Omit<PresenceNotice, "id">;
  replyText?: string;
}

const RULES: Readonly<Record<string, ProjectionRule>> = Object.freeze({
  "assistant.turn.started": {
    activity: "attending",
    statusText: "Reviewing your request",
  },
  "command.created": {
    activity: "attending",
    statusText: "Preparing the next step",
  },
  "command.reclaimed": {
    activity: "attending",
    statusText: "Resuming interrupted work",
  },
  "command.running": {
    activity: "working",
    statusText: "Working in the project",
  },
  "preview.starting": {
    activity: "working",
    statusText: "Preparing the preview",
  },
  "preview.ready": {
    activity: "ready",
    statusText: "Preview ready",
    notice: { tone: "info", text: "Preview is ready" },
  },
  "approval.requested": {
    activity: "needs_attention",
    statusText: "Waiting for your decision",
    notice: { tone: "critical", text: "An approval needs your decision" },
  },
  "command.waiting_approval": {
    activity: "needs_attention",
    statusText: "Waiting for your decision",
    notice: { tone: "critical", text: "An approval needs your decision" },
  },
  "system.action.completed": {
    activity: "ready",
    statusText: "System action complete",
  },
  "assistant.turn.completed": {
    activity: "ready",
    statusText: "Ready for review",
    replyText: "The task update is ready",
  },
  "assistant.turn.cancelled": {
    activity: "ambient",
    statusText: "Standing by",
  },
  "preview.stopped": {
    activity: "ambient",
    statusText: "Standing by",
  },
  "assistant.turn.failed": {
    activity: "needs_attention",
    statusText: "Needs attention",
    notice: { tone: "critical", text: "Fairy needs your attention" },
  },
  "command.failure": {
    activity: "needs_attention",
    statusText: "Needs attention",
    notice: { tone: "critical", text: "Fairy needs your attention" },
  },
  "preview.failed": {
    activity: "needs_attention",
    statusText: "Needs attention",
    notice: { tone: "critical", text: "Preview could not be prepared" },
  },
});

function initialPresenceProjection(): PresenceProjectionState {
  return {
    activity: "ambient",
    status_text: "Standing by",
    last_cursor: 0,
    last_event_id: null,
    updated_at_ms: 0,
    recent_activity_ms: [],
    notice: null,
    reply: null,
  };
}

function reducePresenceProjection(
  state: PresenceProjectionState,
  event: EventEnvelope,
): PresenceProjectionState {
  if (event.visibility !== "user" || event.cursor <= state.last_cursor) return state;
  const rule = RULES[event.event_type];
  if (rule === undefined) return state;
  const occurredAt = Date.parse(event.created_at);
  if (!Number.isFinite(occurredAt) || occurredAt < 0) return state;

  const eventKey = `event:${event.id}`;
  const recentActivity = [...state.recent_activity_ms, occurredAt]
    .filter((value) => value >= occurredAt - DENSITY_WINDOW_MS)
    .slice(-12);
  return {
    activity: rule.activity,
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
        : { id: eventKey, text: rule.replyText },
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
  const afk =
    state.updated_at_ms === 0 || options.now_ms - state.updated_at_ms >= AFK_AFTER_MS;
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
