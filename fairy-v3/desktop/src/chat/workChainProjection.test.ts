import { describe, expect, it } from "vitest";

import type {
  AssistantTurn,
  EventEnvelope,
  TraceStep,
  TurnTrace,
} from "../core/client";
import { projectWorkChain, withVoiceStep } from "./workChainProjection";

describe("projectWorkChain", () => {
  it("deduplicates trace events and never downgrades newer durable state", () => {
    const trace = turnTrace([
      step({
        id: STEP_ID,
        sequence: 1,
        kind: "model",
        status: "succeeded",
        public_summary: "Implementation complete",
        public_detail: "Validated public result",
        updated_at: "2026-07-15T00:00:04Z",
        completed_at: "2026-07-15T00:00:04Z",
        duration_ms: 2_000,
        model_id: "moonshotai/kimi-k2.7-code",
        model_role: "primary",
        provider_attempt_id: ATTEMPT_ID,
      }),
    ]);
    const events = [
      event(3, "turn.trace.step.completed", {
        trace_step_id: STEP_ID,
        trace_id: TRACE_ID,
        turn_id: TURN_ID,
        sequence: 1,
        kind: "model",
        status: "succeeded",
        public_summary: "Implementation complete",
        public_detail: "Validated public result",
      }, "2026-07-15T00:00:03Z"),
      event(1, "turn.trace.step.created", {
        trace_step_id: STEP_ID,
        trace_id: TRACE_ID,
        turn_id: TURN_ID,
        sequence: 1,
        kind: "model",
        status: "pending",
        public_summary: "Starting implementation",
        private_prompt: "must never appear",
      }, "2026-07-15T00:00:01Z"),
      event(2, "turn.trace.step.updated", {
        trace_step_id: STEP_ID,
        trace_id: TRACE_ID,
        turn_id: TURN_ID,
        sequence: 1,
        kind: "model",
        status: "running",
        public_summary: "Implementing changes",
        api_key: "secret",
      }, "2026-07-15T00:00:02Z"),
    ];

    const projection = projectWorkChain({ trace, events, now: Date.parse("2026-07-15T00:00:05Z") });

    expect(projection.steps).toHaveLength(1);
    expect(projection.current.status).toBe("succeeded");
    expect(projection.current.summary).toBe("Implementation complete");
    expect(projection.current.modelId).toBe("moonshotai/kimi-k2.7-code");
    expect(projection.terminal).toBe(true);
    expect(JSON.stringify(projection)).not.toContain("must never appear");
    expect(JSON.stringify(projection)).not.toContain("secret");
  });

  it("shows developer steps only in Developer Mode and always drops internal steps", () => {
    const trace = turnTrace([
      step({ id: STEP_ID, sequence: 1, visibility: "user", public_summary: "Public plan" }),
      step({
        id: DEVELOPER_STEP_ID,
        sequence: 2,
        visibility: "developer",
        kind: "tool",
        public_summary: "Developer verification",
        command_run_id: RUN_ID,
      }),
      step({
        id: INTERNAL_STEP_ID,
        sequence: 3,
        visibility: "internal",
        public_summary: "Hidden provider continuation",
      }),
    ]);
    const commandEvent = event(4, "command.created", {
      command_name: "workspace.files.read",
      arguments: { path: "private.txt" },
    }, "2026-07-15T00:00:04Z", RUN_ID);

    const normal = projectWorkChain({ trace, events: [commandEvent] });
    const developer = projectWorkChain({ trace, events: [commandEvent], developerMode: true });

    expect(normal.steps.map((item) => item.summary)).toEqual(["Public plan"]);
    expect(developer.steps.map((item) => item.summary)).toEqual([
      "Public plan",
      "Developer verification",
    ]);
    expect(developer.steps[1]?.commandName).toBe("workspace.files.read");
    expect(JSON.stringify(developer)).not.toContain("Hidden provider continuation");
    expect(JSON.stringify(developer)).not.toContain("private.txt");
  });

  it("keeps voice playback as a separate causal step", () => {
    const base = projectWorkChain({ trace: turnTrace([step({ id: STEP_ID, sequence: 1 })]) });
    const withVoice = withVoiceStep(base, TURN_ID, "failed", "2026-07-15T00:00:05Z");

    expect(withVoice.steps).toHaveLength(2);
    expect(withVoice.current.kind).toBe("voice");
    expect(withVoice.current.summary).toBe("Voice playback failed");
    expect(withVoice.current.causedByStepId).toBeNull();
  });

  it("counts distinct models instead of model calls", () => {
    const trace = turnTrace([
      step({
        id: STEP_ID,
        sequence: 1,
        kind: "model",
        model_id: "deepseek/deepseek-v4-pro",
        model_role: "coordinator",
      }),
      step({
        id: DEVELOPER_STEP_ID,
        sequence: 2,
        kind: "model",
        model_id: "moonshotai/kimi-k2.7-code",
        model_role: "primary",
      }),
      step({
        id: INTERNAL_STEP_ID,
        sequence: 3,
        kind: "model",
        model_id: "moonshotai/kimi-k2.7-code",
        model_role: "primary",
      }),
    ]);

    expect(projectWorkChain({ trace }).modelCount).toBe(2);
  });

  it("does not mark a running trace terminal between durable steps", () => {
    const trace = {
      ...turnTrace([step({ id: STEP_ID, sequence: 1, status: "succeeded" })]),
      completed_at: null,
    };
    const turn = {
      id: TURN_ID,
      conversation_id: CONVERSATION_ID,
      task_id: TASK_ID,
      status: "running",
      created_at: "2026-07-15T00:00:00Z",
      updated_at: "2026-07-15T00:00:02Z",
      started_at: "2026-07-15T00:00:00Z",
      completed_at: null,
    } as AssistantTurn;

    expect(projectWorkChain({ trace, turn }).terminal).toBe(false);
  });

  it("treats a terminal turn as terminal when its trace has a stale running step", () => {
    const trace = turnTrace([
      step({
        id: STEP_ID,
        status: "running",
        public_summary: "Stale worker activity",
        completed_at: null,
        duration_ms: null,
      }),
    ]);
    const turn = {
      id: TURN_ID,
      conversation_id: CONVERSATION_ID,
      task_id: TASK_ID,
      status: "completed",
      created_at: "2026-07-15T00:00:00Z",
      updated_at: "2026-07-15T00:00:05Z",
      started_at: "2026-07-15T00:00:00Z",
      completed_at: "2026-07-15T00:00:05Z",
    } as AssistantTurn;

    const projection = projectWorkChain({ trace, turn });

    expect(projection.terminal).toBe(true);
    expect(projection.current.status).toBe("succeeded");
    expect(projection.current.summary).toBe("Response ready");
  });

  it.each([
    ["failed", "failed"],
    ["cancelled", "cancelled"],
  ] as const)("stops stale activity for a %s historical turn", (turnStatus, stepStatus) => {
    const trace = turnTrace([
      step({ status: "running", completed_at: null, duration_ms: null }),
    ]);
    const turn = {
      id: TURN_ID,
      conversation_id: CONVERSATION_ID,
      task_id: TASK_ID,
      status: turnStatus,
      created_at: "2026-07-15T00:00:00Z",
      updated_at: "2026-07-15T00:00:05Z",
      started_at: "2026-07-15T00:00:00Z",
      completed_at: "2026-07-15T00:00:05Z",
      error_code: turnStatus === "failed" ? "WORKER_INTERRUPTED" : null,
    } as AssistantTurn;

    const projection = projectWorkChain({ trace, turn });

    expect(projection.terminal).toBe(true);
    expect(projection.current.status).toBe(stepStatus);
    expect(projection.current.summary).toBe(
      turnStatus === "failed" ? "Response failed" : "Response stopped",
    );
  });

  it("settles missing and failed trace loads without leaving active work", () => {
    const missing = projectWorkChain({
      turnId: TURN_ID,
      traceState: { status: "loaded", error: null },
    });
    const failed = projectWorkChain({
      turnId: TURN_ID,
      traceState: { status: "error", error: "Core unavailable" },
    });
    const loading = projectWorkChain({
      turnId: TURN_ID,
      traceState: { status: "loading", error: null },
    });

    expect(missing).toMatchObject({ terminal: true, current: { summary: "No work chain recorded" } });
    expect(failed).toMatchObject({ terminal: true, current: { status: "failed", summary: "Work chain unavailable" } });
    expect(loading).toMatchObject({ terminal: false, current: { status: "running", summary: "Loading work chain" } });
  });
});

const TURN_ID = "019f5ad1-7df8-7000-8000-000000000001";
const TRACE_ID = "019f5ad1-7df8-7000-8000-000000000002";
const TASK_ID = "019f5ad1-7df8-7000-8000-000000000003";
const CONVERSATION_ID = "019f5ad1-7df8-7000-8000-000000000004";
const STEP_ID = "019f5ad1-7df8-7000-8000-000000000005";
const DEVELOPER_STEP_ID = "019f5ad1-7df8-7000-8000-000000000006";
const INTERNAL_STEP_ID = "019f5ad1-7df8-7000-8000-000000000007";
const ATTEMPT_ID = "019f5ad1-7df8-7000-8000-000000000008";
const RUN_ID = "019f5ad1-7df8-7000-8000-000000000009";

function turnTrace(steps: TraceStep[]): TurnTrace {
  return {
    id: TRACE_ID,
    turn_id: TURN_ID,
    conversation_id: CONVERSATION_ID,
    task_id: TASK_ID,
    legacy: false,
    last_sequence: steps.length,
    revision: 1,
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:05Z",
    started_at: "2026-07-15T00:00:00Z",
    completed_at: "2026-07-15T00:00:05Z",
    steps,
  };
}

function step(overrides: Partial<TraceStep>): TraceStep {
  return {
    id: STEP_ID,
    trace_id: TRACE_ID,
    turn_id: TURN_ID,
    sequence: 1,
    parent_step_id: null,
    caused_by_step_id: null,
    kind: "reasoning",
    status: "succeeded",
    public_summary: "Reasoning complete",
    public_detail: null,
    model_id: null,
    model_role: null,
    provider_attempt_id: null,
    command_run_id: null,
    artifact_refs: [],
    visibility: "user",
    revision: 1,
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:01Z",
    started_at: "2026-07-15T00:00:00Z",
    completed_at: "2026-07-15T00:00:01Z",
    duration_ms: 1_000,
    ...overrides,
  };
}

function event(
  cursor: number,
  eventType: string,
  payload: Record<string, unknown>,
  createdAt: string,
  runId: string | null = null,
): EventEnvelope {
  return {
    id: `019f5ad1-7df8-7000-8000-${cursor.toString().padStart(12, "0")}`,
    cursor,
    run_id: runId,
    project_id: null,
    conversation_id: CONVERSATION_ID,
    task_id: TASK_ID,
    version_id: null,
    task_sequence: cursor,
    event_type: eventType,
    visibility: "user",
    message: "Durable event",
    payload,
    schema_version: 1,
    created_at: createdAt,
  };
}
