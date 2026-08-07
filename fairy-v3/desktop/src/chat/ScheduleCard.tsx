import {
  CalendarClock,
  CirclePause,
  CirclePlay,
  Pencil,
  Play,
  X,
} from "lucide-react";
import { useState } from "react";

import type { AssistantSchedule } from "../core/client";
import { ScheduleRuleEditor } from "./ScheduleRuleEditor";
import {
  formatScheduleRule,
  formatScheduleTime,
  type ScheduleRuleDraft,
} from "./scheduleRules";

export interface ScheduleCardActions {
  update(schedule: AssistantSchedule, rule: ScheduleRuleDraft): Promise<void>;
  pause(schedule: AssistantSchedule): Promise<void>;
  resume(schedule: AssistantSchedule): Promise<void>;
  runNow(schedule: AssistantSchedule): Promise<void>;
  cancel(schedule: AssistantSchedule): Promise<void>;
}

export function ScheduleCard({
  schedule,
  busy,
  canRunNow,
  actions,
}: {
  schedule: AssistantSchedule;
  busy: boolean;
  canRunNow?: boolean;
  actions: ScheduleCardActions;
}) {
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mutable = schedule.status === "active" || schedule.status === "paused";

  const act = async (operation: () => Promise<void>) => {
    setError(null);
    try {
      await operation();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Schedule action failed");
    }
  };

  return (
    <article
      id={`schedule-${schedule.id}`}
      className={`schedule-card schedule-card-${schedule.status}`}
      data-schedule-id={schedule.id}
      data-message-sequence={schedule.timeline_sequence}
      tabIndex={-1}
    >
      <div className="schedule-card-icon" aria-hidden="true">
        <CalendarClock size={16} />
      </div>
      <div className="schedule-card-body">
        <header>
          <div>
            <span className="eyebrow">SCHEDULED TASK</span>
            <strong>{schedule.instruction}</strong>
          </div>
          <span className={`schedule-status schedule-status-${schedule.status}`}>
            {schedule.status}
          </span>
        </header>
        <div className="schedule-card-meta">
          <span>{formatScheduleRule(schedule)}</span>
          {schedule.status === "active" ? (
            <span>Next {formatScheduleTime(schedule.next_fire_at)}</span>
          ) : null}
          {schedule.consecutive_failures > 0 ? (
            <span>{schedule.consecutive_failures} consecutive failure(s)</span>
          ) : null}
        </div>
        {schedule.attention_code ? (
          <p className="schedule-card-attention" role="status">
            Needs attention · {schedule.attention_code.replaceAll("_", " ").toLowerCase()}
          </p>
        ) : null}
        {error ? <p className="schedule-card-error" role="alert">{error}</p> : null}
        {editing ? (
          <ScheduleRuleEditor
            key={`${schedule.id}:${schedule.active_revision}`}
            mode="edit"
            schedule={schedule}
            busy={busy}
            onSubmit={async (rule) => {
              await actions.update(schedule, rule);
              setEditing(false);
            }}
            onCancel={() => setEditing(false)}
          />
        ) : mutable ? (
          <div className="schedule-card-actions" role="group" aria-label="Schedule controls">
            <button type="button" disabled={busy} onClick={() => setEditing(true)}>
              <Pencil size={13} /> Edit
            </button>
            {schedule.status === "paused" ? (
              <button type="button" disabled={busy} onClick={() => void act(() => actions.resume(schedule))}>
                <CirclePlay size={13} /> Resume
              </button>
            ) : (
              <button type="button" disabled={busy} onClick={() => void act(() => actions.pause(schedule))}>
                <CirclePause size={13} /> Pause
              </button>
            )}
            <button
              type="button"
              disabled={busy || schedule.status !== "active" || canRunNow === false}
              onClick={() => void act(() => actions.runNow(schedule))}
            >
              <Play size={13} /> Run now
            </button>
            <button type="button" disabled={busy} onClick={() => void act(() => actions.cancel(schedule))}>
              <X size={13} /> Cancel
            </button>
          </div>
        ) : null}
      </div>
    </article>
  );
}
