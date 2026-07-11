import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AssistantTurn, EventEnvelope } from "../core/client";
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
});

describe("useAssistantTurn", () => {
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
      runTurn: async () => {
        order.push("run");
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

    expect(order).toEqual(["task", "document", "turn", "run"]);
    expect(result.current.turn).toEqual(completed);
    expect(result.current.isBusy).toBe(false);
    expect(onTaskCreated).toHaveBeenCalledWith(taskId);
    expect(onSettled).toHaveBeenCalledTimes(1);
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
      runTurn: vi.fn(async (id: string) =>
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
      runTurn: async () => new Promise<AssistantTurn>(() => undefined),
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
});

interface ClientOverrides {
  createTask?: AssistantTurnClient["tasks"]["create"];
  importDocument?: AssistantTurnClient["documents"]["import"];
  createTurn?: AssistantTurnClient["assistant"]["turns"]["create"];
  runTurn?: AssistantTurnClient["assistant"]["turns"]["run"];
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
        run: overrides.runTurn ?? (async () => assistantTurn({ status: "completed" })),
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
