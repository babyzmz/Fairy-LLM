import { useMemo, useState } from "react";

import type { AssistantSchedule } from "../core/client";
import {
  buildScheduleRule,
  defaultScheduleEditorValue,
  formatScheduleTime,
  scheduleEditorValue,
  type ScheduleEditorValue,
  type ScheduleRuleDraft,
} from "./scheduleRules";

interface ScheduleRuleEditorProps {
  mode: "later" | "repeat" | "edit";
  schedule?: AssistantSchedule;
  busy?: boolean;
  onSubmit(value: ScheduleRuleDraft): Promise<void>;
  onCancel(): void;
}

export function ScheduleRuleEditor({
  mode,
  schedule,
  busy = false,
  onSubmit,
  onCancel,
}: ScheduleRuleEditorProps) {
  const initial = useMemo(
    () => schedule === undefined
      ? defaultScheduleEditorValue(mode === "later" ? "later" : "repeat")
      : scheduleEditorValue(schedule),
    [mode, schedule],
  );
  const [value, setValue] = useState<ScheduleEditorValue>(initial);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const preview = useMemo(() => {
    try {
      return formatScheduleTime(buildScheduleRule(value).next_fire_at);
    } catch {
      return "Choose a valid future time";
    }
  }, [value]);
  const repeating = mode !== "later";

  return (
    <div
      className="schedule-rule-editor"
      aria-label={mode === "edit" ? "Edit schedule" : "Schedule task"}
    >
      {repeating ? (
        <label>
          <span>Repeat</span>
          <select
            value={value.kind}
            onChange={(event) => setValue((current) => ({
              ...current,
              kind: event.target.value as ScheduleEditorValue["kind"],
            }))}
          >
            {mode === "edit" ? <option value="once">Once</option> : null}
            <option value="daily">Every day</option>
            <option value="weekdays">Weekdays</option>
            <option value="weekly">Every week</option>
            <option value="interval">Custom interval</option>
          </select>
        </label>
      ) : null}
      {value.kind === "once" ? (
        <label>
          <span>Run at</span>
          <input
            type="datetime-local"
            value={value.onceAt}
            onChange={(event) => setValue((current) => ({
              ...current,
              onceAt: event.target.value,
            }))}
          />
        </label>
      ) : value.kind === "interval" ? (
        <div className="schedule-rule-interval">
          <label>
            <span>Every</span>
            <input
              type="number"
              min={1}
              max={8760}
              value={value.intervalAmount}
              onChange={(event) => setValue((current) => ({
                ...current,
                intervalAmount: Number(event.target.value),
              }))}
            />
          </label>
          <label>
            <span>Unit</span>
            <select
              value={value.intervalUnit}
              onChange={(event) => setValue((current) => ({
                ...current,
                intervalUnit: event.target.value === "days" ? "days" : "hours",
              }))}
            >
              <option value="hours">Hours</option>
              <option value="days">Days</option>
            </select>
          </label>
        </div>
      ) : (
        <div className="schedule-rule-time">
          {value.kind === "weekly" ? (
            <label>
              <span>Day</span>
              <select
                value={value.weekday}
                onChange={(event) => setValue((current) => ({
                  ...current,
                  weekday: Number(event.target.value),
                }))}
              >
                {WEEKDAYS.map((day, index) => (
                  <option key={day} value={index}>{day}</option>
                ))}
              </select>
            </label>
          ) : null}
          <label>
            <span>Local time</span>
            <input
              type="time"
              value={value.localTime}
              onChange={(event) => setValue((current) => ({
                ...current,
                localTime: event.target.value,
              }))}
            />
          </label>
        </div>
      )}
      <p className="schedule-rule-preview">
        Next run <strong>{preview}</strong>
      </p>
      {error ? <p className="schedule-rule-error" role="alert">{error}</p> : null}
      <div className="schedule-rule-actions">
        <button className="secondary-command" type="button" disabled={busy || submitting} onClick={onCancel}>
          Cancel
        </button>
        <button
          className="primary-command"
          type="button"
          disabled={busy || submitting}
          onClick={() => {
            try {
              const rule = buildScheduleRule(value);
              setError(null);
              setSubmitting(true);
              void onSubmit(rule)
                .catch((failure: unknown) => {
                  setError(failure instanceof Error ? failure.message : "Schedule could not be saved");
                })
                .finally(() => setSubmitting(false));
            } catch (failure) {
              setError(failure instanceof Error ? failure.message : "Schedule is invalid");
            }
          }}
        >
          {mode === "edit" ? "Save changes" : "Create schedule"}
        </button>
      </div>
    </div>
  );
}

const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
] as const;
