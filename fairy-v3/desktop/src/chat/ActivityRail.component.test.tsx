import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Message, TraceStep, TurnTrace } from "../core/client";
import { ActivityRail } from "./ActivityRail";
import { MessageList } from "./MessageList";

afterEach(cleanup);

describe("ActivityRail", () => {
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
        streamedText=""
        turn={null}
        turnTraces={{ [TURN_ID]: firstTrace, [SECOND_TURN_ID]: secondTrace }}
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
