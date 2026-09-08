import { describe, expect, it } from "vitest";

import type { EventEnvelope } from "../../core/contracts";

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
    ["unknown event", event("tool.secret_arguments")],
  ])("ignores %s", (_label, incoming) => {
    const initial = PresenceProjection.initial();
    expect(PresenceProjection.reduce(initial, incoming)).toBe(initial);
  });

  it("maps public model deltas to streaming without projecting generated text", () => {
    const state = PresenceProjection.reduce(
      PresenceProjection.initial(),
      event("assistant.message.delta", {
        payload: { text: "private generated text" },
      }),
    );
    expect(state.work_state).toBe("streaming");
    expect(state.status_text).toBe("Writing the reply");
    expect(JSON.stringify(state)).not.toContain("private generated text");
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

  it("shows the durable CommandRun approval phase without exposing its payload", () => {
    const pending = PresenceProjection.reduce(
      PresenceProjection.initial(),
      event("command.waiting_approval"),
    );

    expect(pending.activity).toBe("needs_attention");
    expect(pending.status_text).toBe("Waiting for your decision");
    expect(pending.notice?.text).toBe("An approval needs your decision");
    expect(JSON.stringify(pending)).not.toContain("secret-value");

    const completed = PresenceProjection.reduce(
      pending,
      event("system.action.completed", {
        id: "01989f92-4b80-7000-8000-000000000020",
        cursor: 2,
        created_at: new Date(BASE_TIME + 1_000).toISOString(),
      }),
    );
    expect(completed.activity).toBe("ready");
    expect(completed.status_text).toBe("System action complete");
  });

  it("closes every public Command lifecycle state without leaving Fairy stuck", () => {
    const queued = PresenceProjection.reduce(
      PresenceProjection.initial(),
      event("command.queued"),
    );
    expect(queued.work_state).toBe("analyzing");

    const succeeded = PresenceProjection.reduce(
      queued,
      event("command.succeeded", {
        cursor: 2,
        id: "01989f92-4b80-7000-8000-000000000021",
        payload: { public_summary: "must not be copied" },
      }),
    );
    expect(succeeded.work_state).toBe("ready");
    expect(succeeded.status_text).toBe("Checking the result");
    expect(JSON.stringify(succeeded)).not.toContain("must not be copied");

    const rejected = PresenceProjection.reduce(
      succeeded,
      event("command.rejected", {
        cursor: 3,
        id: "01989f92-4b80-7000-8000-000000000022",
      }),
    );
    expect(rejected.work_state).toBe("idle");
    expect(rejected.notice?.text).toBe("The action was not run");
    expect(presenceProjectionStateSchema.parse(rejected)).toEqual(rejected);
  });

  it("treats routing budget approval as the same safe approval state", () => {
    const pending = PresenceProjection.reduce(
      PresenceProjection.initial(),
      event("assistant.budget.approval_requested"),
    );
    expect(pending.work_state).toBe("awaiting_confirmation");
    expect(pending.notice?.text).toBe("An approval needs your decision");
  });

  it("projects the safe TurnTrace kind and status as one causal work chain", () => {
    const thinking = PresenceProjection.reduce(
      PresenceProjection.initial(),
      event("turn.trace.step.updated", {
        payload: {
          kind: "reasoning",
          status: "running",
          public_summary: "safe but deliberately not copied",
          hidden_reasoning: "must not cross",
        },
      }),
    );
    expect(thinking.work_state).toBe("analyzing");
    expect(JSON.stringify(thinking)).not.toContain("safe but deliberately not copied");
    expect(JSON.stringify(thinking)).not.toContain("must not cross");

    const tool = PresenceProjection.reduce(
      thinking,
      event("turn.trace.step.updated", {
        id: "01989f92-4b80-7000-8000-000000000030",
        cursor: 2,
        created_at: new Date(BASE_TIME + 1_000).toISOString(),
        payload: { kind: "tool", status: "running", arguments: ["secret"] },
      }),
    );
    expect(tool.work_state).toBe("tool");
    expect(JSON.stringify(tool)).not.toContain("secret");

    const approval = PresenceProjection.reduce(
      tool,
      event("turn.trace.step.updated", {
        id: "01989f92-4b80-7000-8000-000000000031",
        cursor: 3,
        created_at: new Date(BASE_TIME + 2_000).toISOString(),
        payload: { kind: "approval", status: "waiting" },
      }),
    );
    expect(approval.work_state).toBe("awaiting_confirmation");

    const resumed = PresenceProjection.reduce(
      approval,
      event("approval.decided", {
        id: "01989f92-4b80-7000-8000-000000000032",
        cursor: 4,
        created_at: new Date(BASE_TIME + 3_000).toISOString(),
      }),
    );
    expect(resumed.work_state).toBe("analyzing");
    expect(resumed.status_text).toBe("Preparing the next step");
  });

  it("projects media generation without copying job payloads", () => {
    const active = PresenceProjection.reduce(
      PresenceProjection.initial(),
      event("media.generation.progress", {
        payload: { job_id: "private-job", kind: "video", progress: 0.4 },
      }),
    );
    expect(active.work_state).toBe("tool");
    expect(active.status_text).toBe("Generating media");
    expect(JSON.stringify(active)).not.toContain("private-job");

    const ready = PresenceProjection.reduce(
      active,
      event("media.generation.completed", {
        id: "01989f92-4b80-7000-8000-000000000040",
        cursor: 2,
        created_at: new Date(BASE_TIME + 1_000).toISOString(),
      }),
    );
    expect(ready.notice?.text).toBe("Media is ready");
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

    const pending = PresenceProjection.reduce(
      failed,
      event("approval.requested", {
        id: "01989f92-4b80-7000-8000-000000000012",
        cursor: 12,
        created_at: new Date(BASE_TIME + 13_000).toISOString(),
      }),
    );
    const stillPending = derivePresenceView(pending, {
      now_ms: BASE_TIME + 6 * 60_000,
      quiet_mode: false,
      dismissed_notice_ids: [],
    });
    expect(stillPending.work_state).toBe("awaiting_confirmation");
    expect(stillPending.notice?.text).toBe("An approval needs your decision");

    const afk = derivePresenceView(completed, {
      now_ms: BASE_TIME + 6 * 60_000,
      quiet_mode: false,
      dismissed_notice_ids: [],
    });
    expect(afk.activity).toBe("ambient");
    expect(afk.density).toBe("quiet");
    expect(afk.status_text).toBe("Standing by");
  });
});
it.each([
  "Fairy is preparing", "Fairy is loading the local model", "Fairy is connecting",
  "Fairy is listening", "Fairy is observing", "Fairy is thinking", "Fairy is searching",
  "Fairy is speaking", "Fairy is standing by", "Realtime privacy pause is active",
  "Realtime resources are limited", "Realtime companion needs attention",
])("transports the fixed Realtime status: %s", (status_text) => {
  expect(presenceProjectionStateSchema.safeParse({
    ...PresenceProjection.initial(), status_text,
  }).success).toBe(true);
});
