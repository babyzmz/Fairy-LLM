import { describe, expect, it } from "vitest";

import { buildScheduleRule, formatScheduleRule } from "./scheduleRules";

describe("schedule rules", () => {
  const now = new Date("2026-08-03T01:15:00.000Z");

  it("builds a future one-shot rule", () => {
    const rule = buildScheduleRule({
      kind: "once",
      onceAt: "2026-08-04T12:30",
      localTime: "09:00",
      weekday: 0,
      intervalAmount: 1,
      intervalUnit: "hours",
    }, now);

    expect(rule.trigger_kind).toBe("once");
    expect(rule.trigger_rule).toEqual({});
    expect(new Date(rule.next_fire_at).getTime()).toBeGreaterThan(now.getTime());
    expect(rule.timezone.length).toBeGreaterThan(0);
  });

  it("uses Python weekday numbering for weekly rules", () => {
    const rule = buildScheduleRule({
      kind: "weekly",
      onceAt: "",
      localTime: "10:45",
      weekday: 4,
      intervalAmount: 1,
      intervalUnit: "hours",
    }, now);

    expect(rule.trigger_rule).toEqual({ local_time: "10:45", weekday: 4 });
    expect(formatScheduleRule({
      trigger_kind: rule.trigger_kind,
      trigger_rule: rule.trigger_rule,
      next_fire_at: rule.next_fire_at,
    })).toBe("Friday at 10:45");
  });

  it("builds bounded hour and day intervals", () => {
    const hours = buildScheduleRule({
      kind: "interval",
      onceAt: "",
      localTime: "09:00",
      weekday: 0,
      intervalAmount: 6,
      intervalUnit: "hours",
    }, now);
    const days = buildScheduleRule({
      kind: "interval",
      onceAt: "",
      localTime: "09:00",
      weekday: 0,
      intervalAmount: 2,
      intervalUnit: "days",
    }, now);

    expect(hours.trigger_rule).toEqual({ amount: 6, unit: "hours" });
    expect(new Date(hours.next_fire_at).getTime() - now.getTime()).toBe(6 * 60 * 60 * 1_000);
    expect(days.trigger_rule).toEqual({ amount: 2, unit: "days" });
  });

  it("rejects past times and invalid intervals", () => {
    expect(() => buildScheduleRule({
      kind: "once",
      onceAt: "2020-01-01T00:00",
      localTime: "09:00",
      weekday: 0,
      intervalAmount: 1,
      intervalUnit: "hours",
    }, now)).toThrow("future date");
    expect(() => buildScheduleRule({
      kind: "interval",
      onceAt: "",
      localTime: "09:00",
      weekday: 0,
      intervalAmount: 0,
      intervalUnit: "hours",
    }, now)).toThrow("positive whole number");
  });
});
