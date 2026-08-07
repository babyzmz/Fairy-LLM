import type {
  AssistantSchedule,
  AssistantScheduleTriggerKind,
  JsonValue,
} from "../core/client";

export interface ScheduleRuleDraft {
  trigger_kind: AssistantScheduleTriggerKind;
  trigger_rule: Record<string, JsonValue>;
  timezone: string;
  next_fire_at: string;
}

export interface ScheduleEditorValue {
  kind: AssistantScheduleTriggerKind;
  onceAt: string;
  localTime: string;
  weekday: number;
  intervalAmount: number;
  intervalUnit: "hours" | "days";
}

export function defaultScheduleEditorValue(
  mode: "later" | "repeat",
): ScheduleEditorValue {
  const next = new Date();
  next.setMinutes(next.getMinutes() + 60, 0, 0);
  return {
    kind: mode === "later" ? "once" : "daily",
    onceAt: datetimeLocal(next),
    localTime: localClock(next),
    weekday: pythonWeekday(next),
    intervalAmount: 1,
    intervalUnit: "hours",
  };
}

export function scheduleEditorValue(schedule: AssistantSchedule): ScheduleEditorValue {
  const next = new Date(schedule.next_fire_at);
  return {
    kind: schedule.trigger_kind,
    onceAt: datetimeLocal(next),
    localTime: String(schedule.trigger_rule.local_time ?? localClock(next)),
    weekday: Number(schedule.trigger_rule.weekday ?? pythonWeekday(next)),
    intervalAmount: Number(schedule.trigger_rule.amount ?? 1),
    intervalUnit: schedule.trigger_rule.unit === "days" ? "days" : "hours",
  };
}

export function buildScheduleRule(
  value: ScheduleEditorValue,
  now = new Date(),
): ScheduleRuleDraft {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  if (value.kind === "once") {
    const next = new Date(value.onceAt);
    if (!Number.isFinite(next.getTime()) || next <= now) {
      throw new Error("Choose a future date and time");
    }
    return {
      trigger_kind: "once",
      trigger_rule: {},
      timezone,
      next_fire_at: next.toISOString(),
    };
  }
  if (value.kind === "interval") {
    if (!Number.isInteger(value.intervalAmount) || value.intervalAmount < 1) {
      throw new Error("Interval must be a positive whole number");
    }
    const next = new Date(now);
    if (value.intervalUnit === "hours") {
      next.setHours(next.getHours() + value.intervalAmount);
    } else {
      next.setDate(next.getDate() + value.intervalAmount);
    }
    return {
      trigger_kind: "interval",
      trigger_rule: { amount: value.intervalAmount, unit: value.intervalUnit },
      timezone,
      next_fire_at: next.toISOString(),
    };
  }
  const [hour, minute] = value.localTime.split(":").map(Number);
  if (
    !Number.isInteger(hour) ||
    !Number.isInteger(minute) ||
    hour < 0 ||
    hour > 23 ||
    minute < 0 ||
    minute > 59
  ) {
    throw new Error("Choose a valid local time");
  }
  const next = new Date(now);
  next.setSeconds(0, 0);
  next.setHours(hour, minute, 0, 0);
  if (next <= now) next.setDate(next.getDate() + 1);
  if (value.kind === "weekdays") {
    while (next.getDay() === 0 || next.getDay() === 6) {
      next.setDate(next.getDate() + 1);
    }
  } else if (value.kind === "weekly") {
    if (!Number.isInteger(value.weekday) || value.weekday < 0 || value.weekday > 6) {
      throw new Error("Choose a valid weekday");
    }
    const targetDay = (value.weekday + 1) % 7;
    const daysAhead = (targetDay - next.getDay() + 7) % 7;
    if (daysAhead > 0) next.setDate(next.getDate() + daysAhead);
    if (next <= now) next.setDate(next.getDate() + 7);
  }
  return {
    trigger_kind: value.kind,
    trigger_rule: {
      local_time: value.localTime,
      ...(value.kind === "weekly" ? { weekday: value.weekday } : {}),
    },
    timezone,
    next_fire_at: next.toISOString(),
  };
}

export function formatScheduleTime(value: string): string {
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return "Invalid date";
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

export function formatScheduleRule(schedule: Pick<
  AssistantSchedule,
  "trigger_kind" | "trigger_rule" | "next_fire_at"
>): string {
  const rule = schedule.trigger_rule;
  if (schedule.trigger_kind === "once") return `Once · ${formatScheduleTime(schedule.next_fire_at)}`;
  if (schedule.trigger_kind === "daily") return `Daily at ${String(rule.local_time ?? "--:--")}`;
  if (schedule.trigger_kind === "weekdays") {
    return `Weekdays at ${String(rule.local_time ?? "--:--")}`;
  }
  if (schedule.trigger_kind === "weekly") {
    const weekday = Number(rule.weekday ?? -1);
    return `${WEEKDAY_LABELS[weekday] ?? "Weekly"} at ${String(rule.local_time ?? "--:--")}`;
  }
  return `Every ${String(rule.amount ?? "?")} ${String(rule.unit ?? "hours")}`;
}

function datetimeLocal(value: Date): string {
  const offset = value.getTimezoneOffset() * 60_000;
  return new Date(value.getTime() - offset).toISOString().slice(0, 16);
}

function localClock(value: Date): string {
  return `${String(value.getHours()).padStart(2, "0")}:${String(value.getMinutes()).padStart(2, "0")}`;
}

function pythonWeekday(value: Date): number {
  return (value.getDay() + 6) % 7;
}

const WEEKDAY_LABELS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
] as const;
