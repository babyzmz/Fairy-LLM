import { describe, expect, it } from "vitest";

import type {
  AssistantBackgroundTask,
  AssistantBackgroundTaskPage,
} from "../core/client";
import { BackgroundTaskNotificationTracker } from "./backgroundNotifications";

describe("BackgroundTaskNotificationTracker", () => {
  it("uses the first durable projection as a silent baseline", () => {
    const tracker = new BackgroundTaskNotificationTracker();
    const page = taskPage(task({ status: "completed" }));

    expect(tracker.observe(page, () => false)).toEqual([]);
  });

  it("notifies once for completion, failure, approval, and context attention", () => {
    const tracker = new BackgroundTaskNotificationTracker();
    tracker.observe(taskPage(
      task({ id: "turn:1", status: "running" }),
      task({ id: "turn:2", status: "running" }),
      task({ id: "turn:3", status: "running" }),
      task({ id: "schedule:4", status: "scheduled" }),
    ), () => false);

    const notifications = tracker.observe(taskPage(
      task({ id: "turn:1", status: "completed" }),
      task({ id: "turn:2", status: "failed", public_error: "The provider timed out." }),
      task({ id: "turn:3", status: "waiting_for_approval" }),
      task({
        id: "schedule:4",
        status: "attention_required",
        turn_id: null,
        attention_code: "VERSION_INVALID",
      }),
    ), () => false);

    expect(notifications.map((item) => item.title)).toEqual([
      "Background task completed",
      "Background task failed",
      "Fairy needs approval",
      "Scheduled task paused",
    ]);
    expect(notifications[1]?.body).toContain("The provider timed out.");
    expect(notifications[3]?.turn_id).toBeNull();
    expect(tracker.observe(taskPage(), () => false)).toEqual([]);
  });

  it("suppresses a transition while its chat is actively viewed", () => {
    const tracker = new BackgroundTaskNotificationTracker();
    tracker.observe(taskPage(task({ status: "running" })), () => false);

    expect(tracker.observe(
      taskPage(task({ status: "completed" })),
      (item) => item.conversation_id === CONVERSATION_ID,
    )).toEqual([]);
    expect(tracker.observe(taskPage(task({ status: "completed" })), () => false)).toEqual([]);
  });

  it("does not notify for queued, running, paused, scheduled, or cancelled states", () => {
    const tracker = new BackgroundTaskNotificationTracker();
    tracker.observe(taskPage(), () => false);

    for (const status of ["queued", "running", "paused", "scheduled", "cancelled"]) {
      expect(tracker.observe(taskPage(task({ id: `turn:${status}`, status })), () => false))
        .toEqual([]);
    }
  });
});

const CONVERSATION_ID = "019f7b34-9300-7000-8000-000000000010";

function task(overrides: Partial<AssistantBackgroundTask> = {}): AssistantBackgroundTask {
  return {
    id: "turn:1",
    kind: "turn",
    conversation_id: CONVERSATION_ID,
    conversation_title: "Research chat",
    project_id: null,
    task_id: "019f7b34-9300-7000-8000-000000000011",
    turn_id: "019f7b34-9300-7000-8000-000000000012",
    workflow_run_id: null,
    schedule_id: null,
    occurrence_id: null,
    title: "Verify the current implementation",
    status: "running",
    public_error: null,
    attention_code: null,
    current_conversation: false,
    scheduled_for: null,
    next_fire_at: null,
    schedule_revision: null,
    turn_status: "running",
    turn_cancellation_revision: 0,
    workflow_budget_tier: "normal",
    created_at: "2026-08-07T00:00:00Z",
    updated_at: "2026-08-07T00:01:00Z",
    can_pause: true,
    can_resume: false,
    can_cancel: true,
    can_run_now: false,
    ...overrides,
  };
}

function taskPage(...items: AssistantBackgroundTask[]): AssistantBackgroundTaskPage {
  return { current: [], other: items, recent: [], nonterminal_count: items.length };
}
