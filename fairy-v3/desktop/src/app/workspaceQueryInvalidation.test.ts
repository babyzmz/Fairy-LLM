import { describe, expect, it } from "vitest";

import type { EventEnvelope } from "../core/client";
import {
  addWorkspaceEventInvalidation,
  createWorkspaceInvalidationBatch,
  workspaceQueryMatchesInvalidation,
} from "./workspaceQueryInvalidation";

describe("workspace query invalidation", () => {
  it("refreshes global task projections and only the changed chat schedule cards", () => {
    const batch = createWorkspaceInvalidationBatch();
    addWorkspaceEventInvalidation(batch, event("assistant.schedule.changed", {
      conversation_id: "conversation-a",
    }));
    expect(matches(batch, ["workspace", "background-tasks", "conversation-a"])).toBe(true);
    expect(matches(batch, ["workspace", "background-tasks", "conversation-b"])).toBe(true);
    expect(matches(batch, ["workspace", "assistant-schedules", "conversation-a"])).toBe(true);
    expect(matches(batch, ["workspace", "assistant-schedules", "conversation-b"])).toBe(false);
    expect(matches(batch, ["workspace", "providers"])).toBe(false);
    expect(matches(batch, ["workspace", "messages", "conversation-a"])).toBe(false);
  });

  it.each(["assistant.turn.completed", "assistant.turn.paused", "approval.created"])(
    "refreshes background task status from %s without text delta polling", (type) => {
      const batch = createWorkspaceInvalidationBatch();
      addWorkspaceEventInvalidation(batch, event(type, { conversation_id: "conversation-a" }));
      expect(matches(batch, ["workspace", "background-tasks", "conversation-b"])).toBe(true);
    },
  );

  it("limits a created message to its Conversation, history, Tasks, and traces", () => {
    const batch = createWorkspaceInvalidationBatch();
    addWorkspaceEventInvalidation(batch, event("message.created", {
      conversation_id: "conversation-a",
      task_id: "task-a",
    }));

    expect(matches(batch, ["workspace", "messages", "conversation-a"])).toBe(true);
    expect(matches(batch, ["workspace", "project-messages", "conversation-a"])).toBe(true);
    expect(matches(batch, ["workspace", "messages", "conversation-b"])).toBe(false);
    expect(matches(batch, ["workspace", "tasks"])).toBe(true);
    expect(matches(batch, ["workspace", "tasks", "conversation-a"])).toBe(true);
    expect(matches(batch, ["workspace", "tasks", "conversation-b"])).toBe(false);
    expect(matches(batch, ["workspace", "task-detail", "conversation-a", "task-a"])).toBe(true);
    expect(matches(batch, ["workspace", "task-detail", "conversation-b", "task-b"])).toBe(false);
    expect(matches(batch, ["workspace", "turn-trace", "turn-a"])).toBe(true);
    expect(matches(batch, ["workspace", "conversations"])).toBe(true);
    expect(matches(batch, ["workspace", "providers"])).toBe(false);
    expect(matches(batch, ["workspace", "preview", "conversation-a", "task-a"])).toBe(false);
  });

  it("limits Preview events to the matching Task runtime surfaces", () => {
    const batch = createWorkspaceInvalidationBatch();
    addWorkspaceEventInvalidation(batch, event("preview.ready", {
      conversation_id: "conversation-a",
      task_id: "task-a",
    }));

    expect(matches(batch, ["workspace", "preview", "conversation-a", "task-a", "version-a"])).toBe(true);
    expect(matches(batch, ["workspace", "runtime-health", "conversation-a", "task-a"])).toBe(true);
    expect(matches(batch, ["workspace", "preview", "conversation-a", "task-b", "version-b"])).toBe(false);
    expect(matches(batch, ["workspace", "messages", "conversation-a"])).toBe(false);
    expect(matches(batch, ["settings", "category", "models"])).toBe(false);
  });

  it("refreshes only Knowledge projections and settings for Knowledge events", () => {
    const batch = createWorkspaceInvalidationBatch();
    addWorkspaceEventInvalidation(batch, event("knowledge.sync.completed", {
      project_id: "project-a",
    }));

    expect(matches(batch, ["workspace", "knowledge", "graph", "project-a", "watermark-a"])).toBe(true);
    expect(matches(batch, ["workspace", "knowledge", "graph", "project-b", "watermark-b"])).toBe(false);
    expect(matches(batch, ["workspace", "obsidian", "sources", "project-a"])).toBe(true);
    expect(matches(batch, ["settings", "category", "knowledge"])).toBe(true);
    expect(matches(batch, ["settings", "category", "extensions"])).toBe(false);
  });

  it("does not invalidate queries for streaming text deltas", () => {
    const batch = createWorkspaceInvalidationBatch();
    addWorkspaceEventInvalidation(batch, event("assistant.message.delta", {
      conversation_id: "conversation-a",
      task_id: "task-a",
    }));

    expect(matches(batch, ["workspace", "messages", "conversation-a"])).toBe(false);
    expect(matches(batch, ["workspace", "tasks"])).toBe(false);
    expect(matches(batch, ["workspace", "turn-trace", "turn-a"])).toBe(false);
  });

  it("refreshes generated assets for the matching media Conversation", () => {
    const batch = createWorkspaceInvalidationBatch();
    addWorkspaceEventInvalidation(batch, event("media.generation.completed", {
      conversation_id: "conversation-a",
      task_id: "task-a",
    }));

    expect(matches(batch, ["workspace", "media-jobs", "conversation-a", "task-a"])).toBe(true);
    expect(matches(batch, ["workspace", "asset-sets", "conversation-a", "workspace-a", "version-a"])).toBe(true);
    expect(matches(batch, ["workspace", "asset-sets", "conversation-b", "workspace-b", "version-b"])).toBe(false);
  });

  it("refreshes the active browser session and snapshot for browser events", () => {
    const batch = createWorkspaceInvalidationBatch();
    addWorkspaceEventInvalidation(batch, event("browser.session.updated", {
      conversation_id: "conversation-a",
      task_id: "task-a",
    }));

    expect(matches(batch, ["workspace", "browser-sessions", "conversation-a", "task-a"])).toBe(true);
    expect(matches(batch, ["workspace", "browser-sessions", "conversation-b", "task-b"])).toBe(false);
    expect(matches(batch, ["workspace", "browser-snapshot", "session-a", "tab-a", "revision-a"])).toBe(true);
  });

  it("refreshes only the matching Conversation transcript for appended captions", () => {
    const batch = createWorkspaceInvalidationBatch();
    addWorkspaceEventInvalidation(batch, event("realtime.transcript.appended", {
      conversation_id: "conversation-a",
    }));

    expect(matches(batch, ["workspace", "realtime-transcript", "conversation-a"])).toBe(true);
    expect(matches(batch, ["workspace", "realtime-transcript", "conversation-b"])).toBe(false);
    expect(matches(batch, ["workspace", "messages", "conversation-a"])).toBe(false);
    expect(matches(batch, ["workspace", "tasks"])).toBe(false);
    expect(matches(batch, ["settings", "category", "voice"])).toBe(false);
    expect(matches(batch, ["workspace", "providers"])).toBe(false);
  });

  it("coalesces multiple conversations without widening to unrelated queries", () => {
    const batch = createWorkspaceInvalidationBatch();
    addWorkspaceEventInvalidation(batch, event("message.created", {
      conversation_id: "conversation-a",
      task_id: "task-a",
    }));
    addWorkspaceEventInvalidation(batch, event("message.created", {
      conversation_id: "conversation-b",
      task_id: "task-b",
    }));

    expect(matches(batch, ["workspace", "messages", "conversation-a"])).toBe(true);
    expect(matches(batch, ["workspace", "messages", "conversation-b"])).toBe(true);
    expect(matches(batch, ["workspace", "messages", "conversation-c"])).toBe(false);
    expect(matches(batch, ["workspace", "provider-health"])).toBe(false);
  });
});

function matches(
  batch: ReturnType<typeof createWorkspaceInvalidationBatch>,
  queryKey: readonly unknown[],
): boolean {
  return workspaceQueryMatchesInvalidation(queryKey, batch);
}

function event(
  eventType: string,
  scope: Partial<Pick<EventEnvelope, "conversation_id" | "project_id" | "task_id">> = {},
): EventEnvelope {
  return {
    id: `event-${eventType}`,
    cursor: 1,
    task_sequence: null,
    schema_version: 1,
    visibility: "user",
    project_id: scope.project_id ?? null,
    conversation_id: scope.conversation_id ?? null,
    task_id: scope.task_id ?? null,
    version_id: null,
    run_id: null,
    created_at: "2026-07-23T00:00:00Z",
    event_type: eventType,
    message: eventType,
    payload: {},
  };
}
