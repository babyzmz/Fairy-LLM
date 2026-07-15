import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  AssistantTurn,
  EventEnvelope,
  ModelSelectionPreference,
} from "../core/client";
import {
  assistantDeltaText,
  type AssistantTurnClient,
  useAssistantTurn,
} from "./useAssistantTurn";

const conversationId = "00000000-0000-4000-8000-000000000010";
const taskId = "00000000-0000-4000-8000-000000000020";
const turnId = "00000000-0000-4000-8000-000000000030";

describe("assistantDeltaText", () => {
  it("orders chunks and deduplicates both replayed events and chunk coordinates", () => {
    const events = [
      deltaEvent("event-3", 13, turnId, 0, 2, "world"),
      deltaEvent("event-1", 11, turnId, 0, 0, "Hello "),
      deltaEvent("event-1", 11, turnId, 0, 0, "Hello "),
      deltaEvent("event-2", 12, turnId, 0, 1, "durable "),
      deltaEvent("event-4", 14, turnId, 0, 1, "duplicate chunk"),
      deltaEvent("event-5", 10, "another-turn", 0, 0, "ignored"),
    ];

    expect(assistantDeltaText(events, turnId)).toBe("Hello durable world");
  });

  it("drops provisional text after a projection reset", () => {
    const reset = {
      ...deltaEvent("reset", 2, turnId, 0, 0, ""),
      event_type: "assistant.message.projection_reset",
      payload: { turn_id: turnId },
    };

    expect(
      assistantDeltaText(
        [
          deltaEvent("draft", 1, turnId, 0, 0, "Draft response"),
          reset,
          deltaEvent("final", 3, turnId, 1, 0, "Reviewed response"),
        ],
        turnId,
      ),
    ).toBe("Reviewed response");
  });
});

describe("useAssistantTurn", () => {
  it("projects the user message before task creation returns", async () => {
    let resolveTask: ((value: never) => void) | null = null;
    const createTask = vi.fn(
      () => new Promise<never>((resolve) => { resolveTask = resolve; }),
    );
    const { result } = renderHook(() =>
      useAssistantTurn({
        client: assistantClient({ createTask }),
        conversationId,
        profileId: "openrouter-free",
        operationMode: "answer",
        events: [],
      }),
    );

    act(() => { void result.current.send("Visible immediately", []); });

    expect(result.current.pendingUserMessage).toMatchObject({
      content: "Visible immediately",
      status: "sending",
    });
    expect(result.current.isBusy).toBe(true);
    expect(resolveTask).not.toBeNull();
  });

  it("can bind a pet-origin turn to an explicitly selected scratch conversation", async () => {
    const petConversationId = "00000000-0000-4000-8000-000000000099";
    const createTask = vi.fn<AssistantTurnClient["tasks"]["create"]>(
      async () => ({ task: { id: taskId } }) as never,
    );
    const { result } = renderHook(() =>
      useAssistantTurn({
        client: assistantClient({ createTask }),
        conversationId: null,
        profileId: "openrouter-free",
        operationMode: "answer",
        events: [],
      }),
    );

    await act(async () => {
      await result.current.sendToConversation(
        petConversationId,
        "Scratch only",
        [],
      );
    });

    expect(createTask).toHaveBeenCalledWith(
      expect.objectContaining({
        conversation_id: petConversationId,
        operation_mode: "answer",
        user_request: "Scratch only",
      }),
    );
  });

  it("keeps a failed optimistic message available for edit", async () => {
    const file = new File(["draft"], "draft.txt", { type: "text/plain" });
    const { result } = renderHook(() =>
      useAssistantTurn({
        client: assistantClient({
          createTask: async () => { throw new Error("Core unavailable"); },
        }),
        conversationId,
        profileId: "openrouter-free",
        operationMode: "answer",
        events: [],
      }),
    );

    let failure: unknown = null;
    await act(async () => {
      try {
        await result.current.send("Keep this", [file]);
      } catch (caught) {
        failure = caught;
      }
    });
    expect(failure).toBeInstanceOf(Error);
    expect(result.current.pendingUserMessage).toMatchObject({
      content: "Keep this",
      status: "failed",
      error: "Core unavailable",
    });

    let draft = null;
    act(() => { draft = result.current.takePendingForEdit(); });
    expect(draft).toMatchObject({ value: "Keep this", files: [file] });
    expect(result.current.pendingUserMessage).toBeNull();
  });

  it("creates a task, imports attachments, creates the turn, and runs it in order", async () => {
    const order: string[] = [];
    const created = assistantTurn({ status: "created" });
    const completed = assistantTurn({
      status: "completed",
      completed_at: "2026-07-11T00:00:03Z",
    });
    const client = assistantClient({
      createTask: async (input) => {
        order.push("task");
        expect(input.operation_mode).toBe("answer");
        expect(input.conversation_id).toBe(conversationId);
        return { task: { id: taskId } } as never;
      },
      importDocument: async (input) => {
        order.push("document");
        expect(input.task_id).toBe(taskId);
        expect(input.filename).toBe("notes.txt");
        expect(input.content_base64).toBe("RmFpcnk=");
        return {} as never;
      },
      createTurn: async (input) => {
        order.push("turn");
        expect(input).toMatchObject({
          task_id: taskId,
          profile_id: "openrouter-free",
          image_attachments: [
            {
              media_type: "image/png",
              png_base64: "iVBORw0KGgo=",
              content_hash: "a".repeat(64),
              width: 1280,
              height: 720,
              source_label: "Game window",
              captured_at_ms: 1_784_000_000_000,
              persistence: "ephemeral",
            },
          ],
        });
        return created;
      },
      startTurn: async () => {
        order.push("start");
        return completed;
      },
    });
    const onTaskCreated = vi.fn();
    const onSettled = vi.fn();
    const { result } = renderHook(() =>
      useAssistantTurn({
        client,
        conversationId,
        profileId: "openrouter-free",
        operationMode: "answer",
        events: [],
        onTaskCreated,
        onSettled,
      }),
    );

    await act(async () => {
      await result.current.send(
        "Read this",
        [new File(["Fairy"], "notes.txt", { type: "text/plain" })],
        [
          {
            kind: "window",
            source_id: "2",
            source_label: "Game window",
            media_type: "image/png",
            png_base64: "iVBORw0KGgo=",
            width: 1280,
            height: 720,
            byte_length: 8,
            content_hash: "a".repeat(64),
            captured_at_ms: 1_784_000_000_000,
            persistence: "ephemeral",
          },
        ],
      );
    });

    expect(order).toEqual(["task", "document", "turn", "start"]);
    expect(result.current.turn).toEqual(completed);
    expect(result.current.isBusy).toBe(false);
    expect(onTaskCreated).toHaveBeenCalledWith(taskId);
    expect(onSettled).toHaveBeenCalledTimes(1);
  });

  it("captures the global model selection before asynchronous preparation", async () => {
    let resolveTask: ((value: never) => void) | null = null;
    const createTask = vi.fn<AssistantTurnClient["tasks"]["create"]>(
      () => new Promise((resolve) => { resolveTask = resolve; }),
    );
    const createTurn = vi.fn<AssistantTurnClient["assistant"]["turns"]["create"]>(
      async () => assistantTurn(),
    );
    const initialSelection = selection("auto", null, 7);
    const { result, rerender } = renderHook(
      ({ modelSelection }: { modelSelection: ModelSelectionPreference }) =>
        useAssistantTurn({
          client: assistantClient({ createTask, createTurn }),
          conversationId,
          modelSelection,
          operationMode: "answer",
          events: [],
        }),
      { initialProps: { modelSelection: initialSelection } },
    );

    act(() => { void result.current.send("Route this", []); });
    rerender({
      modelSelection: selection("manual", "z-ai/glm-5.2", 8),
    });
    await act(async () => {
      resolveTask?.({ task: { id: taskId } } as never);
    });
    await waitFor(() => expect(createTurn).toHaveBeenCalledTimes(1));

    expect(createTurn).toHaveBeenCalledWith(
      expect.objectContaining({
        task_id: taskId,
        model_selection: { mode: "auto", model_id: null, revision: 7 },
      }),
    );
    expect(createTurn.mock.calls[0]?.[0]).not.toHaveProperty("profile_id");
  });

  it("cancels with the current revision and retries a terminal turn", async () => {
    const running = assistantTurn({ status: "running" });
    const cancelled = assistantTurn({ status: "cancelled" });
    const retried = assistantTurn({ id: "00000000-0000-4000-8000-000000000031" });
    const completed = assistantTurn({
      id: retried.id,
      status: "completed",
      completed_at: "2026-07-11T00:00:04Z",
    });
    const cancelTurn = vi.fn(async () => cancelled);
    const retryTurn = vi.fn(async () => retried);
    const client = assistantClient({
      createTask: async () => ({ task: { id: taskId } }) as never,
      createTurn: async () => running,
      startTurn: vi.fn(async (id: string) =>
        id === retried.id ? completed : new Promise<AssistantTurn>(() => undefined),
      ),
      cancelTurn,
      retryTurn,
    });
    const { result } = renderHook(() =>
      useAssistantTurn({
        client,
        conversationId,
        profileId: "openrouter-free",
        operationMode: "answer",
        events: [],
      }),
    );

    act(() => {
      void result.current.send("Try once", []);
    });
    await waitFor(() => expect(result.current.turn?.status).toBe("running"));

    await act(async () => result.current.cancel());
    expect(cancelTurn).toHaveBeenCalledWith({
      turn_id: turnId,
      expected_cancellation_revision: running.cancellation_revision,
    });

    await act(async () => result.current.retry());
    expect(retryTurn).toHaveBeenCalledWith({
      turn_id: turnId,
      idempotency_key: expect.stringMatching(/^desktop:assistant-retry:/),
    });
    expect(result.current.turn).toEqual(completed);
  });

  it("derives the active stream without duplicating replayed SSE chunks", async () => {
    const created = assistantTurn({ status: "running" });
    const client = assistantClient({
      createTask: async () => ({ task: { id: taskId } }) as never,
      createTurn: async () => created,
      startTurn: async () => new Promise<AssistantTurn>(() => undefined),
    });
    const events = [
      deltaEvent("event-a", 1, turnId, 0, 0, "One "),
      deltaEvent("event-b", 2, turnId, 0, 1, "response"),
      deltaEvent("event-c", 3, turnId, 0, 1, "response again"),
    ];
    const { result } = renderHook(() =>
      useAssistantTurn({
        client,
        conversationId,
        profileId: "openrouter-free",
        operationMode: "answer",
        events,
      }),
    );

    act(() => {
      void result.current.send("Stream", []);
    });
    await waitFor(() => expect(result.current.turn?.id).toBe(turnId));

    expect(result.current.streamedText).toBe("One response");
    expect(result.current.isBusy).toBe(true);
  });

  it("removes the stream projection after the durable turn completes", async () => {
    const completed = assistantTurn({
      status: "completed",
      completed_at: "2026-07-11T00:00:04Z",
    });
    const client = assistantClient({
      createTask: async () => ({ task: { id: taskId } }) as never,
      createTurn: async () => assistantTurn({ status: "running" }),
      startTurn: async () => completed,
    });
    const { result } = renderHook(() =>
      useAssistantTurn({
        client,
        conversationId,
        profileId: "openrouter-free",
        operationMode: "answer",
        events: [deltaEvent("event-a", 1, turnId, 0, 0, "Durable response")],
      }),
    );

    await act(async () => result.current.send("Complete", []));

    expect(result.current.turn).toEqual(completed);
    expect(result.current.streamedText).toBe("");
  });

  it("settles from a durable terminal event after start returns", async () => {
    const created = assistantTurn({ status: "created" });
    const completed = assistantTurn({
      status: "completed",
      completed_at: "2026-07-11T00:00:04Z",
    });
    const client = assistantClient({
      createTask: async () => ({ task: { id: taskId } }) as never,
      createTurn: async () => created,
      startTurn: async () => created,
      getTurn: async () => completed,
    });
    const { result, rerender } = renderHook(
      ({ events }: { events: EventEnvelope[] }) =>
        useAssistantTurn({
          client,
          conversationId,
          profileId: "openrouter-free",
          operationMode: "answer",
          events,
        }),
      { initialProps: { events: [] as EventEnvelope[] } },
    );

    await act(async () => result.current.send("Finish asynchronously", []));
    expect(result.current.isBusy).toBe(true);
    rerender({
      events: [{
        ...deltaEvent("terminal", 20, turnId, 1, 1, ""),
        event_type: "assistant.turn.completed",
        payload: { turn_id: turnId, message_id: "message-1" },
      }],
    });

    await waitFor(() => expect(result.current.turn).toEqual(completed));
    expect(result.current.isBusy).toBe(false);
    expect(result.current.streamedText).toBe("");
  });

  it("resumes a waiting turn after an explicit approval action", async () => {
    const waiting = assistantTurn({ status: "waiting_for_tool" });
    const completed = assistantTurn({
      status: "completed",
      completed_at: "2026-07-11T00:00:04Z",
    });
    const startTurn = vi
      .fn<AssistantTurnClient["assistant"]["turns"]["start"]>()
      .mockResolvedValueOnce(waiting)
      .mockResolvedValueOnce(completed);
    const client = assistantClient({
      createTask: async () => ({ task: { id: taskId } }) as never,
      createTurn: async () => assistantTurn(),
      startTurn,
    });
    const { result } = renderHook(() =>
      useAssistantTurn({
        client,
        conversationId,
        profileId: "openrouter-free",
        operationMode: "answer",
        events: [],
      }),
    );

    await act(async () => result.current.send("Notify me", []));
    expect(result.current.turn?.status).toBe("waiting_for_tool");

    await act(async () => result.current.resume());

    await waitFor(() => expect(result.current.turn).toEqual(completed));
    expect(startTurn).toHaveBeenCalledTimes(2);
  });
});

interface ClientOverrides {
  createTask?: AssistantTurnClient["tasks"]["create"];
  importDocument?: AssistantTurnClient["documents"]["import"];
  createTurn?: AssistantTurnClient["assistant"]["turns"]["create"];
  getTurn?: AssistantTurnClient["assistant"]["turns"]["get"];
  startTurn?: AssistantTurnClient["assistant"]["turns"]["start"];
  cancelTurn?: AssistantTurnClient["assistant"]["turns"]["cancel"];
  retryTurn?: AssistantTurnClient["assistant"]["turns"]["retry"];
}

function assistantClient(overrides: ClientOverrides): AssistantTurnClient {
  return {
    tasks: {
      create: overrides.createTask ?? (async () => ({ task: { id: taskId } }) as never),
    },
    documents: {
      import: overrides.importDocument ?? (async () => ({} as never)),
    },
    assistant: {
      turns: {
        create: overrides.createTurn ?? (async () => assistantTurn()),
        get: overrides.getTurn ?? (async () => assistantTurn({ status: "completed" })),
        start: overrides.startTurn ?? (async () => assistantTurn({ status: "completed" })),
        cancel:
          overrides.cancelTurn ?? (async () => assistantTurn({ status: "cancelled" })),
        retry: overrides.retryTurn ?? (async () => assistantTurn()),
      },
    },
  };
}

function assistantTurn(overrides: Partial<AssistantTurn> = {}): AssistantTurn {
  return {
    id: turnId,
    task_id: taskId,
    conversation_id: conversationId,
    profile_id: "openrouter-free",
    status: "created",
    idempotency_key: "desktop:assistant:create",
    scope_digest: "scope-digest",
    memory_snapshot_id: "00000000-0000-4000-8000-000000000040",
    memory_snapshot_hash: "memory-hash",
    model_selection: null,
    routing_decision: null,
    budget_approval_run_id: null,
    cancellation_revision: 2,
    usage: {},
    created_at: "2026-07-11T00:00:00Z",
    updated_at: "2026-07-11T00:00:00Z",
    started_at: null,
    completed_at: null,
    error_code: null,
    ...overrides,
  };
}

function selection(
  mode: ModelSelectionPreference["mode"],
  modelId: string | null,
  revision: number,
): ModelSelectionPreference {
  return {
    mode,
    model_id: modelId,
    allow_free_fallback: false,
    zero_data_retention: false,
    revision,
    updated_at: "2026-07-11T00:00:00Z",
  };
}

function deltaEvent(
  id: string,
  cursor: number,
  targetTurnId: string,
  modelRound: number,
  chunkIndex: number,
  value: string,
): EventEnvelope {
  return {
    id,
    cursor,
    task_sequence: cursor,
    schema_version: 1,
    visibility: "user",
    project_id: null,
    conversation_id: conversationId,
    task_id: taskId,
    version_id: null,
    run_id: null,
    created_at: "2026-07-11T00:00:00Z",
    event_type: "assistant.message.delta",
    message: "Assistant response delta",
    payload: {
      turn_id: targetTurnId,
      model_round: modelRound,
      chunk_index: chunkIndex,
      text: value,
    },
  };
}
