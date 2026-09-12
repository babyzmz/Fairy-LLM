import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Message, TraceStep, TurnTrace } from "../core/client";
import { ActivityRail } from "./ActivityRail";
import { MessageList } from "./MessageList";

afterEach(cleanup);

describe("ActivityRail", () => {
  it("does not show running controls after the Turn failed ahead of its workflow summary", () => {
    const turn = workflowTurn("running", "failed");
    render(<ActivityRail turn={turn} onPause={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Pause task" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel task" })).not.toBeInTheDocument();
    expect(screen.queryByText("Running")).not.toBeInTheDocument();
  });

  it("shows friendly public steps and gates diagnostics behind Developer Mode", () => {
    const trace = makeTrace([
      makeStep({
        id: MODEL_STEP_ID,
        sequence: 1,
        kind: "model",
        public_summary: "Implemented the requested changes",
        model_id: "moonshotai/kimi-k2.7-code",
        model_role: "primary",
        provider_attempt_id: ATTEMPT_ID,
      }),
      makeStep({
        id: TOOL_STEP_ID,
        sequence: 2,
        parent_step_id: MODEL_STEP_ID,
        kind: "tool",
        public_summary: "Workspace check complete",
        command_run_id: RUN_ID,
      }),
    ]);
    const view = render(<ActivityRail trace={trace} />);

    fireEvent.click(screen.getByRole("button", { name: "Fairy activity" }));
    expect(screen.getByText("Kimi implementation")).toBeInTheDocument();
    expect(screen.queryByText(/moonshotai\/kimi-k2\.7-code/)).not.toBeInTheDocument();
    expect(screen.queryByText(/provider attempt/i)).not.toBeInTheDocument();

    view.rerender(<ActivityRail trace={trace} developerMode />);
    expect(screen.getByText(/model moonshotai\/kimi-k2\.7-code/)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`attempt ${ATTEMPT_ID.slice(0, 8)}`))).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`run ${RUN_ID.slice(0, 8)}`))).toBeInTheDocument();
  });

  it("places each historical work chain after its user message", () => {
    const firstTrace = makeTrace([makeStep({ id: MODEL_STEP_ID, public_summary: "First response ready" })]);
    const secondTrace = {
      ...makeTrace([makeStep({ id: TOOL_STEP_ID, public_summary: "Second response ready" })]),
      id: SECOND_TRACE_ID,
      turn_id: SECOND_TURN_ID,
      steps: [
        {
          ...makeStep({ id: TOOL_STEP_ID, public_summary: "Second response ready" }),
          trace_id: SECOND_TRACE_ID,
          turn_id: SECOND_TURN_ID,
        },
      ],
    };
    const view = render(
      <MessageList
        messages={[
          message(1, "user", "First request", TURN_ID),
          message(2, "assistant", "First answer", TURN_ID),
          message(3, "user", "Second request", SECOND_TURN_ID),
          message(4, "assistant", "Second answer", SECOND_TURN_ID),
        ]}
        realtimeTranscript={[]}
        streamedText=""
        turn={null}
        turnTraces={{ [TURN_ID]: firstTrace, [SECOND_TURN_ID]: secondTrace }}
        turnTraceStates={{
          [TURN_ID]: { status: "loaded", error: null },
          [SECOND_TURN_ID]: { status: "loaded", error: null },
        }}
        events={[]}
        pendingUserMessage={null}
        developerMode={false}
        onRetryPending={vi.fn(async () => undefined)}
        onEditPending={vi.fn()}
        onDeletePending={vi.fn()}
        onCopy={vi.fn(async () => undefined)}
        onOpenLink={vi.fn(async () => undefined)}
      />,
    );

    const order = [...view.container.querySelectorAll("[data-message-sequence], [data-turn-id]")]
      .map((node) => node.getAttribute("data-message-sequence") ?? node.getAttribute("data-turn-id"));
    expect(order).toEqual(["1", TURN_ID, "2", "3", SECOND_TURN_ID, "4"]);
  });

  it("does not animate a terminal turn with a stale running trace step", () => {
    const trace = makeTrace([
      makeStep({ status: "running", completed_at: null, duration_ms: null }),
    ]);
    render(
      <ActivityRail
        trace={trace}
        turn={{
          id: TURN_ID,
          conversation_id: CONVERSATION_ID,
          task_id: TASK_ID,
          status: "completed",
          created_at: "2026-07-15T00:00:00Z",
          updated_at: "2026-07-15T00:00:04Z",
          started_at: "2026-07-15T00:00:00Z",
          completed_at: "2026-07-15T00:00:04Z",
        } as never}
      />,
    );

    expect(document.querySelector(".activity-spinner")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Fairy work chain")).toHaveClass("activity-terminal");
  });

  it("shows a settled trace error without a spinner", () => {
    render(
      <ActivityRail
        turnId={TURN_ID}
        traceState={{ status: "error", error: "Core unavailable" }}
      />,
    );

    expect(screen.getByText("Work chain unavailable")).toBeVisible();
    expect(document.querySelector(".activity-spinner")).not.toBeInTheDocument();
  });

  it("shows Workflow progress, parallel branches, budgets, and scoped controls", () => {
    const onPause = vi.fn(async () => undefined);
    const onCancel = vi.fn(async () => undefined);
    const trace = {
      ...makeTrace([
        makeStep({ id: MODEL_STEP_ID, status: "running", completed_at: null }),
        makeStep({ id: TOOL_STEP_ID, sequence: 2, status: "running", completed_at: null }),
      ]),
      completed_at: null,
    };
    render(
      <ActivityRail
        trace={trace}
        turn={workflowTurn("running")}
        onPause={onPause}
        onResume={vi.fn(async () => undefined)}
        onCancel={onCancel}
      />,
    );

    expect(screen.getByText("2 parallel branches")).toBeVisible();
    expect(screen.getByText("1/4 nodes")).toBeVisible();
    expect(screen.getByText("2/24 rounds")).toBeVisible();
    expect(screen.getByText("5/96 tools")).toBeVisible();
    expect(screen.getByText("Deep")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Pause task" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel task" }));

    expect(onPause).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("resumes a paused Workflow without exposing controls on historical rails", () => {
    const onResume = vi.fn(async () => undefined);
    const view = render(
      <ActivityRail
        trace={makeTrace([])}
        turn={workflowTurn("paused")}
        onResume={onResume}
        onCancel={vi.fn(async () => undefined)}
      />,
    );

    expect(screen.getByText("Paused by you")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Resume task" }));
    expect(onResume).toHaveBeenCalledTimes(1);

    view.rerender(<ActivityRail trace={makeTrace([])} turn={workflowTurn("paused")} />);
    expect(screen.queryByRole("button", { name: "Resume task" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel task" })).not.toBeInTheDocument();
  });
});

const TURN_ID = "019f5ad1-7df8-7000-8000-000000000101";
const SECOND_TURN_ID = "019f5ad1-7df8-7000-8000-000000000102";
const TRACE_ID = "019f5ad1-7df8-7000-8000-000000000103";
const SECOND_TRACE_ID = "019f5ad1-7df8-7000-8000-000000000104";
const TASK_ID = "019f5ad1-7df8-7000-8000-000000000105";
const CONVERSATION_ID = "019f5ad1-7df8-7000-8000-000000000106";
const MODEL_STEP_ID = "019f5ad1-7df8-7000-8000-000000000107";
const TOOL_STEP_ID = "019f5ad1-7df8-7000-8000-000000000108";
const ATTEMPT_ID = "019f5ad1-7df8-7000-8000-000000000109";
const RUN_ID = "019f5ad1-7df8-7000-8000-000000000110";

function makeTrace(steps: TraceStep[]): TurnTrace {
  return {
    id: TRACE_ID,
    turn_id: TURN_ID,
    conversation_id: CONVERSATION_ID,
    task_id: TASK_ID,
    legacy: false,
    last_sequence: steps.length,
    revision: 1,
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:04Z",
    started_at: "2026-07-15T00:00:00Z",
    completed_at: "2026-07-15T00:00:04Z",
    evidence_sources: [],
    steps,
  };
}

function makeStep(overrides: Partial<TraceStep>): TraceStep {
  return {
    id: MODEL_STEP_ID,
    trace_id: TRACE_ID,
    turn_id: TURN_ID,
    sequence: 1,
    parent_step_id: null,
    caused_by_step_id: null,
    kind: "reasoning",
    status: "succeeded",
    public_summary: "Work complete",
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

function workflowTurn(status: "running" | "paused", turnStatus = "running") {
  return {
    id: TURN_ID,
    conversation_id: CONVERSATION_ID,
    task_id: TASK_ID,
    status: turnStatus,
    workflow_summary: {
      run_id: "019f5ad1-7df8-7000-8000-000000000120",
      status,
      budget_tier: "deep",
      active_plan_revision: 2,
      current_phase: "Researching",
      public_summary: "Comparing current sources",
      completed_nodes: 1,
      total_nodes: 4,
      model_rounds_used: 2,
      max_model_rounds: 24,
      tool_invocations_used: 5,
      max_tool_invocations: 96,
      pause_requested: status === "paused",
      updated_at: "2026-07-15T00:00:02Z",
    },
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:02Z",
    started_at: "2026-07-15T00:00:00Z",
    completed_at: null,
  } as never;
}

function message(
  sequence: number,
  role: Message["role"],
  content: string,
  turnId: string,
): Message {
  return {
    id: `019f5ad1-7df8-7000-8000-${(200 + sequence).toString().padStart(12, "0")}`,
    conversation_id: CONVERSATION_ID,
    task_id: TASK_ID,
    turn_id: turnId,
    sequence,
    role,
    visibility: "user",
    content,
    created_at: `2026-07-15T00:00:0${sequence}Z`,
  };
}
