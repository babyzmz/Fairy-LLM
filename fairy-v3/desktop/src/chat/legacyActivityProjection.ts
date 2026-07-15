import type { AssistantTurn, EventEnvelope } from "../core/client";

export interface PublicActivity {
  id: string;
  label: string;
  tone: "active" | "tool" | "success" | "error" | "muted";
  createdAt: string;
}

export function publicActivities(
  turn: AssistantTurn,
  events: EventEnvelope[],
): PublicActivity[] {
  const runNames = commandNamesByRun(events);
  const activities: PublicActivity[] = [];
  const turnCreatedAt = timestamp(turn.created_at);
  const relevant = events
    .filter((event) => event.task_id === turn.task_id)
    .filter((event) => {
      const createdAt = timestamp(event.created_at);
      return turnCreatedAt === 0 || createdAt === 0 || createdAt >= turnCreatedAt;
    })
    .sort((left, right) => left.cursor - right.cursor);

  for (const event of relevant) {
    const activity = activityFromEvent(
      event,
      safeText(event.payload.command_name, 128) ?? runNames.get(event.run_id ?? "") ?? null,
    );
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
    const summary = safeText(event.payload.public_summary, 240);
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

function commandNamesByRun(events: EventEnvelope[]): Map<string, string> {
  const names = new Map<string, string>();
  for (const event of events) {
    const name = safeText(event.payload.command_name, 128);
    if (event.run_id !== null && name !== null) names.set(event.run_id, name);
  }
  return names;
}

function commandLabel(value: string | null): string {
  if (value === null || value === "") return "tool";
  return value
    .split(".")
    .slice(-2)
    .join(" ")
    .replaceAll("_", " ");
}

function safeText(value: unknown, maxLength: number): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length === 0 ? null : trimmed.slice(0, maxLength);
}

function timestamp(value: string): number {
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}
