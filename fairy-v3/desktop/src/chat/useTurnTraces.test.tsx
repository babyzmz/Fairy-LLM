import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";

import type { Message, TurnTrace } from "../core/client";
import { useTurnTraces } from "./useTurnTraces";

describe("useTurnTraces", () => {
  it("keeps a delayed trace response under its requested turn after switching conversations", async () => {
    let resolveA: (trace: TurnTrace) => void = () => {
      throw new Error("trace resolver was not initialized");
    };
    const delayedA = new Promise<TurnTrace>((resolve) => {
      resolveA = resolve;
    });
    const trace = vi.fn(async (turnId: string) =>
      turnId === TURN_A ? delayedA : turnTrace(TURN_B, CONVERSATION_B));
    const { result, rerender } = renderHook(
      ({ messages }: { messages: Message[] }) =>
        useTurnTraces({ assistant: { turns: { trace } } }, options(messages)),
      {
        initialProps: { messages: [message(TURN_A, CONVERSATION_A)] },
        wrapper: queryWrapper(),
      },
    );

    expect(result.current.turnTraceStates[TURN_A]?.status).toBe("loading");
    rerender({ messages: [message(TURN_B, CONVERSATION_B)] });
    await waitFor(() => expect(result.current.turnTraceStates[TURN_B]?.status).toBe("loaded"));
    expect(result.current.turnTraces[TURN_B]?.conversation_id).toBe(CONVERSATION_B);

    await act(async () => { resolveA(turnTrace(TURN_A, CONVERSATION_A)); });

    expect(result.current.turnTraces[TURN_A]).toBeUndefined();
    expect(result.current.turnTraceStates[TURN_A]).toBeUndefined();
    expect(result.current.turnTraces[TURN_B]?.conversation_id).toBe(CONVERSATION_B);
  });

  it("settles a failed trace request as error without retrying forever", async () => {
    const trace = vi.fn(async () => { throw new Error("Trace unavailable"); });
    const { result } = renderHook(
      () => useTurnTraces(
        { assistant: { turns: { trace } } },
        options([message(TURN_A, CONVERSATION_A)]),
      ),
      { wrapper: queryWrapper() },
    );

    await waitFor(() => expect(result.current.turnTraceStates[TURN_A]?.status).toBe("error"));
    expect(result.current.turnTraceStates[TURN_A]?.error).toBe("Trace unavailable");
    expect(result.current.turnTraces[TURN_A]).toBeUndefined();
    expect(trace).toHaveBeenCalledTimes(1);
  });
});

const TURN_A = "00000000-0000-4000-8000-0000000000a1";
const TURN_B = "00000000-0000-4000-8000-0000000000b1";
const CONVERSATION_A = "00000000-0000-4000-8000-0000000000a2";
const CONVERSATION_B = "00000000-0000-4000-8000-0000000000b2";
const TASK_ID = "00000000-0000-4000-8000-0000000000c1";

function options(messages: Message[]) {
  return {
    enabled: true,
    chatMessages: messages,
    projectMessages: [],
    chatTurn: null,
    projectTurn: null,
    selectedTaskId: null,
  };
}

function queryWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  return function Wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function message(turnId: string, conversationId: string): Message {
  return {
    id: `${turnId.slice(0, -1)}9`,
    conversation_id: conversationId,
    task_id: TASK_ID,
    turn_id: turnId,
    sequence: 1,
    role: "user",
    visibility: "user",
    content: "Request",
    created_at: "2026-07-15T00:00:00Z",
  };
}

function turnTrace(turnId: string, conversationId: string): TurnTrace {
  return {
    id: `${turnId.slice(0, -1)}8`,
    turn_id: turnId,
    conversation_id: conversationId,
    task_id: TASK_ID,
    legacy: false,
    last_sequence: 0,
    revision: 0,
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:01Z",
    started_at: "2026-07-15T00:00:00Z",
    completed_at: "2026-07-15T00:00:01Z",
    steps: [],
  };
}
