import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AssistantSchedule } from "../core/client";
import { ScheduleCard } from "./ScheduleCard";

afterEach(cleanup);

describe("ScheduleCard", () => {
  it("exposes future controls without turning the schedule into a message", async () => {
    const user = userEvent.setup();
    const pause = vi.fn(async () => undefined);
    const runNow = vi.fn(async () => undefined);
    const cancel = vi.fn(async () => undefined);
    render(
      <ScheduleCard
        schedule={schedule()}
        busy={false}
        actions={{
          update: vi.fn(async () => undefined),
          pause,
          resume: vi.fn(async () => undefined),
          runNow,
          cancel,
        }}
      />,
    );

    expect(screen.getByText("Summarize the workspace")).toBeVisible();
    expect(screen.getByText("Daily at 09:30")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Pause" }));
    await user.click(screen.getByRole("button", { name: "Run now" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(pause).toHaveBeenCalledWith(expect.objectContaining({ id: SCHEDULE_ID })));
    expect(runNow).toHaveBeenCalledTimes(1);
    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it("edits only the future rule through the schedule revision", async () => {
    const user = userEvent.setup();
    const update = vi.fn(async () => undefined);
    render(
      <ScheduleCard
        schedule={schedule()}
        busy={false}
        actions={{
          update,
          pause: vi.fn(async () => undefined),
          resume: vi.fn(async () => undefined),
          runNow: vi.fn(async () => undefined),
          cancel: vi.fn(async () => undefined),
        }}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update).toHaveBeenCalledWith(
      expect.objectContaining({ id: SCHEDULE_ID, active_revision: 3 }),
      expect.objectContaining({ trigger_kind: "daily" }),
    );
  });
});

const SCHEDULE_ID = "019f7b34-9300-7000-8000-000000000070";

export function schedule(overrides: Partial<AssistantSchedule> = {}): AssistantSchedule {
  return {
    id: SCHEDULE_ID,
    conversation_id: "019f7b34-9300-7000-8000-000000000010",
    task_id: null,
    project_id: null,
    workspace_id: "019f7b34-9300-7000-8000-000000000011",
    version_id: null,
    instruction: "Summarize the workspace",
    operation_mode: "answer",
    trigger_kind: "daily",
    trigger_rule: { local_time: "09:30" },
    timezone: "Australia/Sydney",
    next_fire_at: "2026-08-08T23:30:00Z",
    execution_target: "local",
    profile_id: null,
    model_selection: null,
    permission_profile: "standard",
    timeline_sequence: 2,
    status: "active",
    active_revision: 3,
    consecutive_failures: 0,
    attention_code: null,
    created_at: "2026-08-07T00:00:00Z",
    updated_at: "2026-08-07T00:00:00Z",
    last_fire_at: null,
    paused_at: null,
    completed_at: null,
    cancelled_at: null,
    ...overrides,
  };
}
