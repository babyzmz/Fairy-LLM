import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  CircleDot,
  LoaderCircle,
  Wrench,
} from "lucide-react";
import { useMemo, useState } from "react";

import type { AssistantTurn, EventEnvelope } from "../core/client";

export interface PublicActivity {
  id: string;
  label: string;
  tone: "active" | "tool" | "success" | "error" | "muted";
  createdAt: string;
}

interface ActivityRailProps {
  turn: AssistantTurn;
  events: EventEnvelope[];
}

export function ActivityRail({ turn, events }: ActivityRailProps) {
  const [expanded, setExpanded] = useState(false);
  const activities = useMemo(() => publicActivities(turn, events), [events, turn]);
  const current = activities.at(-1) ?? fallbackActivity(turn);
  const previous = activities.length > 1 ? (activities.at(-2) ?? null) : null;
  const terminal = ["completed", "cancelled", "failed"].includes(turn.status);

  return (
    <section
      className={`activity-rail activity-${current.tone}${terminal ? " activity-terminal" : ""}`}
      aria-label="Fairy activity"
    >
      <button
        type="button"
        className="activity-summary"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <ActivityIcon tone={current.tone} active={!terminal} />
        <span className="activity-current">{current.label}</span>
        {!terminal && previous !== null ? (
          <span className="activity-recent">{previous.label}</span>
        ) : null}
        <ChevronDown className={expanded ? "activity-chevron expanded" : "activity-chevron"} size={14} />
      </button>
      {expanded ? (
        <ol className="activity-history">
          {activities.map((activity) => (
            <li key={activity.id}>
              <ActivityIcon tone={activity.tone} active={false} />
              <span>{activity.label}</span>
              <time dateTime={activity.createdAt}>{formatTime(activity.createdAt)}</time>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}

export function publicActivities(
  turn: AssistantTurn,
  events: EventEnvelope[],
): PublicActivity[] {
  const runNames = new Map<string, string>();
  const activities: PublicActivity[] = [];
  const turnCreatedAt = Date.parse(turn.created_at);
  const relevant = events
    .filter((event) => event.task_id === turn.task_id)
    .filter((event) => {
      const createdAt = Date.parse(event.created_at);
      return Number.isNaN(turnCreatedAt) || Number.isNaN(createdAt) || createdAt >= turnCreatedAt;
    })
    .sort((left, right) => left.cursor - right.cursor);

  for (const event of relevant) {
    const commandName = stringPayload(event, "command_name");
    if (event.run_id !== null && commandName !== null) runNames.set(event.run_id, commandName);
    const activity = activityFromEvent(event, commandName ?? runNames.get(event.run_id ?? "") ?? null);
    if (activity === null) continue;
    const previous = activities.at(-1);
    if (previous?.label === activity.label && previous.tone === activity.tone) continue;
    activities.push(activity);
  }
  return activities;
}

function activityFromEvent(
  event: EventEnvelope,
  commandName: string | null,
): PublicActivity | null {
  const activity = (label: string, tone: PublicActivity["tone"]): PublicActivity => ({
    id: event.id,
    label,
    tone,
    createdAt: event.created_at,
  });
  if (event.event_type === "assistant.turn.started") return activity("Preparing response", "active");
  if (event.event_type === "assistant.message.delta") return activity("Writing response", "active");
  if (event.event_type === "assistant.turn.completed") return activity("Response ready", "success");
  if (event.event_type === "assistant.turn.cancelled") return activity("Response stopped", "muted");
  if (event.event_type === "assistant.turn.failed") return activity("Response failed", "error");
  if (["approval.requested", "command.waiting_approval"].includes(event.event_type)) {
    return activity("Waiting for approval", "tool");
  }
  if (event.event_type === "approval.decided") return activity("Approval received", "success");
  if (event.event_type === "command.created") {
    return commandName === "model.generate"
      ? activity("Analyzing request", "active")
      : activity(`Preparing ${commandLabel(commandName)}`, "tool");
  }
  if (["command.running", "command.reclaimed"].includes(event.event_type)) {
    return commandName === "model.generate"
      ? activity("Generating response", "active")
      : activity(`Using ${commandLabel(commandName)}`, "tool");
  }
  if (event.event_type === "command.succeeded") {
    const summary = stringPayload(event, "public_summary");
    return activity(
      summary ?? (commandName === "model.generate" ? "Generation complete" : "Tool completed"),
      "success",
    );
  }
  if (["command.failed", "command.rejected", "command.interrupted"].includes(event.event_type)) {
    return activity(commandName === "model.generate" ? "Generation failed" : "Tool failed", "error");
  }
  return null;
}

function fallbackActivity(turn: AssistantTurn): PublicActivity {
  const states: Record<AssistantTurn["status"], [string, PublicActivity["tone"]]> = {
    created: ["Starting", "active"],
    running: ["Generating response", "active"],
    waiting_for_tool: ["Waiting for approval", "tool"],
    completed: ["Response ready", "success"],
    cancelled: ["Response stopped", "muted"],
    failed: ["Response failed", "error"],
  };
  const [label, tone] = states[turn.status];
  return { id: `fallback:${turn.id}`, label, tone, createdAt: turn.updated_at };
}

function commandLabel(value: string | null): string {
  if (value === null || value === "") return "tool";
  return value
    .split(".")
    .slice(-2)
    .join(" ")
    .replaceAll("_", " ");
}

function stringPayload(event: EventEnvelope, key: string): string | null {
  const value = event.payload[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function ActivityIcon({
  tone,
  active,
}: {
  tone: PublicActivity["tone"];
  active: boolean;
}) {
  if (active) return <LoaderCircle className="activity-spinner" size={14} />;
  if (tone === "tool") return <Wrench size={14} />;
  if (tone === "success") return <CheckCircle2 size={14} />;
  if (tone === "error") return <AlertCircle size={14} />;
  return <CircleDot size={14} />;
}

function formatTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return "";
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(parsed);
}
