import { describe, expect, it } from "vitest";

import type { EventEnvelope } from "../core/contracts";

import {
  PresenceProjection,
  derivePresenceView,
  presenceProjectionStateSchema,
} from "./projection";

const BASE_TIME = Date.parse("2026-07-11T08:00:00.000Z");

function event(
  eventType: string,
  overrides: Partial<EventEnvelope> = {},
): EventEnvelope {
  return {
    id: "01989f92-4b80-7000-8000-000000000001",
    cursor: 1,
    run_id: null,
    project_id: null,
    conversation_id: "01989f92-4b80-7000-8000-000000000002",
    task_id: "01989f92-4b80-7000-8000-000000000003",
    version_id: null,
    task_sequence: 1,
    schema_version: 1,
    visibility: "user",
    created_at: new Date(BASE_TIME).toISOString(),
    event_type: eventType,
    message: "private model reasoning must never be projected",
    payload: {
      arguments: ["--token", "secret-value"],
      hidden_reasoning: "chain of thought",
    },
    ...overrides,
  };
}

describe("PresenceProjection", () => {
  it("maps only allowlisted durable user events to canned activity", () => {
    const initial = PresenceProjection.initial();
    const started = PresenceProjection.reduce(
      initial,
      event("assistant.turn.started"),
    );

    expect(started.activity).toBe("attending");
    expect(started.status_text).toBe("Reviewing your request");
    expect(JSON.stringify(started)).not.toContain("secret-value");
    expect(JSON.stringify(started)).not.toContain("chain of thought");
    expect(JSON.stringify(started)).not.toContain("private model reasoning");

    const running = PresenceProjection.reduce(
      started,
      event("command.running", {
        id: "01989f92-4b80-7000-8000-000000000004",
        cursor: 2,
        created_at: new Date(BASE_TIME + 1_000).toISOString(),
      }),
    );
    expect(running.activity).toBe("working");
    expect(running.status_text).toBe("Working in the project");
  });

  it.each([
    ["internal event", event("command.running", { visibility: "internal" })],
    ["developer event", event("command.running", { visibility: "developer" })],
    ["model delta", event("assistant.message.delta")],
    ["unknown event", event("tool.secret_arguments")],
  ])("ignores %s", (_label, incoming) => {
    const initial = PresenceProjection.initial();
    expect(PresenceProjection.reduce(initial, incoming)).toBe(initial);
  });

  it("deduplicates cursors and projects failure without copying its message", () => {
    const failed = PresenceProjection.reduce(
      PresenceProjection.initial(),
      event("assistant.turn.failed"),
    );

    expect(failed.activity).toBe("needs_attention");
    expect(failed.notice).toEqual({
      id: "event:01989f92-4b80-7000-8000-000000000001",
      tone: "critical",
      text: "Fairy needs your attention",
    });
    expect(PresenceProjection.reduce(failed, event("command.running"))).toBe(
      failed,
    );
  });

  it("rejects arbitrary cross-window text even when the shape is otherwise valid", () => {
    const state = PresenceProjection.reduce(
      PresenceProjection.initial(),
      event("assistant.turn.completed"),
    );

    expect(
      presenceProjectionStateSchema.safeParse({
        ...state,
        status_text: "model supplied status with a secret",
      }).success,
    ).toBe(false);
    expect(
      presenceProjectionStateSchema.safeParse({
        ...state,
        reply: { id: "reply", text: "arbitrary model output" },
      }).success,
    ).toBe(false);
  });

  it("derives busy density, quiet filtering, dismissed notices, and AFK state", () => {
    let state = PresenceProjection.initial();
    for (let index = 0; index < 4; index += 1) {
      state = PresenceProjection.reduce(
        state,
        event("command.running", {
          id: `01989f92-4b80-7000-8000-00000000000${index + 1}`,
          cursor: index + 1,
          created_at: new Date(BASE_TIME + index * 1_000).toISOString(),
        }),
      );
    }

    expect(
      derivePresenceView(state, {
        now_ms: BASE_TIME + 5_000,
        quiet_mode: false,
        dismissed_notice_ids: [],
      }).density,
    ).toBe("busy");

    const completed = PresenceProjection.reduce(
      state,
      event("assistant.turn.completed", {
        id: "01989f92-4b80-7000-8000-000000000010",
        cursor: 10,
        created_at: new Date(BASE_TIME + 10_000).toISOString(),
      }),
    );
    const quiet = derivePresenceView(completed, {
      now_ms: BASE_TIME + 11_000,
      quiet_mode: true,
      dismissed_notice_ids: [],
    });
    expect(quiet.reply).toBeNull();

    const failed = PresenceProjection.reduce(
      completed,
      event("command.failure", {
        id: "01989f92-4b80-7000-8000-000000000011",
        cursor: 11,
        created_at: new Date(BASE_TIME + 12_000).toISOString(),
      }),
    );
    const dismissed = derivePresenceView(failed, {
      now_ms: BASE_TIME + 13_000,
      quiet_mode: false,
      dismissed_notice_ids: [
        "event:01989f92-4b80-7000-8000-000000000011",
      ],
    });
    expect(dismissed.notice).toBeNull();

    const afk = derivePresenceView(failed, {
      now_ms: BASE_TIME + 6 * 60_000,
      quiet_mode: false,
      dismissed_notice_ids: [],
    });
    expect(afk.activity).toBe("ambient");
    expect(afk.density).toBe("quiet");
    expect(afk.status_text).toBe("Standing by");
  });
});
