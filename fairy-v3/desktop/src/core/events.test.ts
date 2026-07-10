import { describe, expect, it } from "vitest";

import { applyEvent, createEventState, type CoreEvent } from "./events";

function event(overrides: Partial<CoreEvent> = {}): CoreEvent {
  return {
    id: "event-1",
    cursor: 1,
    run_id: "run-1",
    project_id: "project-1",
    conversation_id: "conversation-1",
    task_id: "task-1",
    version_id: "version-1",
    task_sequence: 1,
    event_type: "command.running",
    visibility: "user",
    message: "Running review",
    payload: { status: "running" },
    schema_version: 1,
    created_at: "2026-07-10T00:00:00Z",
    ...overrides,
  };
}

describe("event reducer", () => {
  it("deduplicates events by durable cursor", () => {
    const first = applyEvent(createEventState(), event());
    const duplicate = applyEvent(first, event());

    expect(duplicate).toBe(first);
    expect(duplicate.timeline).toHaveLength(1);
  });

  it("never exposes internal events in the work timeline", () => {
    const state = applyEvent(
      createEventState(),
      event({ cursor: 2, visibility: "internal", message: "private planner state" }),
    );

    expect(state.lastCursor).toBe(2);
    expect(state.timeline).toEqual([]);
  });

  it("updates task status from typed command events", () => {
    const state = applyEvent(createEventState(), event());

    expect(state.taskStatus["task-1"]).toBe("running");
  });
});
