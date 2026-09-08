import { describe, expect, it } from "vitest";

import type { AssistantTurn, EventEnvelope } from "../core/client";
import { publicActivities } from "./ActivityRail";

describe("publicActivities", () => {
  it("summarizes durable lifecycle events without exposing arguments or debug payloads", () => {
    const events = [
      event(1, "command.created", {
        command_name: "web.search",
        arguments: { query: "private query" },
        api_key: "secret",
      }),
      event(2, "command.running", { command_name: "web.search", status: "running" }),
      event(3, "command.succeeded", {
        command_name: "web.search",
        status: "succeeded",
        public_summary: "Research complete",
        model_content: "untrusted raw result",
      }),
      event(4, "assistant.message.delta", {
        turn_id: TURN.id,
        text: "private generated text",
      }),
      event(5, "assistant.message.delta", {
        turn_id: TURN.id,
        text: "another chunk",
      }),
    ];

    const activities = publicActivities(TURN, events);

    expect(activities.map((activity) => activity.label)).toEqual([
      "Preparing web search",
      "Using web search",
      "Research complete",
      "Writing response",
    ]);
    expect(JSON.stringify(activities)).not.toContain("private query");
    expect(JSON.stringify(activities)).not.toContain("secret");
    expect(JSON.stringify(activities)).not.toContain("generated text");
  });
});

const TURN: AssistantTurn = {
  id: "0198f4de-0114-7000-8000-000000000011",
  conversation_id: "0198f4de-0114-7000-8000-000000000012",
  task_id: "0198f4de-0114-7000-8000-000000000013",
  profile_id: "provider",
  scope_digest: "a".repeat(64),
  memory_snapshot_id: "0198f4de-0114-7000-8000-000000000014",
  memory_snapshot_hash: "b".repeat(64),
  idempotency_key: "turn:test",
  model_selection: null,
  routing_decision: null,
  budget_approval_run_id: null,
  execution_engine_version: 2,
  status: "running",
  cancellation_revision: 0,
  cancellation_pending: false,
  cited_evidence_receipt_ids: [],
  usage: {},
  error_code: null,
  created_at: "2026-07-12T00:00:00Z",
  updated_at: "2026-07-12T00:00:01Z",
  started_at: "2026-07-12T00:00:01Z",
  completed_at: null,
};

function event(
  cursor: number,
  eventType: string,
  payload: Record<string, unknown>,
): EventEnvelope {
  return {
    id: `0198f4de-0114-7000-8000-${cursor.toString().padStart(12, "0")}`,
    cursor,
    run_id: "0198f4de-0114-7000-8000-000000000020",
    project_id: null,
    conversation_id: TURN.conversation_id,
    task_id: TURN.task_id,
    version_id: null,
    task_sequence: cursor,
    event_type: eventType,
    visibility: "user",
    message: "Public lifecycle update",
    payload,
    schema_version: 1,
    created_at: `2026-07-12T00:00:0${cursor}Z`,
  };
}
