import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, expect, it, vi } from "vitest";

import type { WorkspaceClient } from "./workspaceTypes";
import type { Conversation } from "../core/client";
import { useAssistantScheduling } from "./useAssistantScheduling";

afterEach(() => { cleanup(); vi.useRealTimers(); });

it("uses a low-frequency recovery fallback and stops it outside chat mode", async () => {
  vi.useFakeTimers();
  const background = vi.fn(async () => ({ current: [], other: [], recent: [], nonterminal_count: 0 }));
  const schedules = vi.fn(async () => ({ items: [] }));
  const client = { assistant: { backgroundTasks: { list: background }, schedules: { list: schedules } } } as unknown as WorkspaceClient;
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const wrapper = ({ children }: PropsWithChildren) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  const input = {
    client, enabled: true, currentConversationId: "chat-a", selectedProfileId: null,
    navigationScopeKey: "chat:chat-a",
    modelSelection: null,
    runAction: <T,>(operation: () => Promise<T>) => operation(),
    invalidateHistory: async () => {}, setMode: vi.fn(), setProjectSelection: vi.fn(),
    setConversationSelection: vi.fn(), setChatConversationSelection: vi.fn(),
    setTaskSelection: vi.fn(), setChatTaskId: vi.fn(),
  };
  const hook = renderHook((props) => useAssistantScheduling(props), { initialProps: input, wrapper });
  try {
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(background).toHaveBeenCalledTimes(1);
    expect(schedules).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(20_010); });
    expect(background).toHaveBeenCalledTimes(2);
    expect(schedules).toHaveBeenCalledTimes(2);
    hook.rerender({ ...input, enabled: false });
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(background).toHaveBeenCalledTimes(2);
    expect(schedules).toHaveBeenCalledTimes(2);
  } finally {
    hook.unmount(); queryClient.clear();
  }
});

it("resolves notification targets without a global task cache and ignores superseded navigation", async () => {
  let resolveFirst!: (value: Conversation) => void;
  let rejectFirst!: (error: Error) => void;
  const errors: unknown[] = [];
  const conversation = (id: string) => ({
    id, project_id: "project", active_task_id: `latest-${id}`, deleted_at: null, purged_at: null,
  }) as Conversation;
  const get = vi.fn(async (id: string) => id === "a"
    ? new Promise<Conversation>((resolve, reject) => { resolveFirst = resolve; rejectFirst = reject; }) : conversation(id));
  const client = {
    conversations: { get },
    assistant: { turns: { get: vi.fn(async () => ({ conversation_id: "b", task_id: "older-b" })) } },
  } as unknown as WorkspaceClient;
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const wrapper = ({ children }: PropsWithChildren) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  const select = vi.fn();
  const selectTask = vi.fn();
  const hook = renderHook(({ scope }) => useAssistantScheduling({
    client, enabled: false, currentConversationId: "current", selectedProfileId: null,
    navigationScopeKey: scope,
    modelSelection: null,
    runAction: async <T,>(operation: () => Promise<T>) => {
      try { return await operation(); } catch (error) { errors.push(error); throw error; }
    },
    invalidateHistory: async () => {}, setMode: vi.fn(), setProjectSelection: vi.fn(),
    setConversationSelection: select, setChatConversationSelection: vi.fn(),
    setTaskSelection: selectTask, setChatTaskId: vi.fn(),
  }), { wrapper, initialProps: { scope: "chat:current" } });
  try {
    act(() => hook.result.current.openBackgroundTaskLocation("a", null));
    await waitFor(() => expect(get).toHaveBeenCalledWith("a"));
    act(() => hook.result.current.openBackgroundTaskLocation("b", "turn-b"));
    await waitFor(() => expect(select).toHaveBeenCalledWith("b"));
    expect(selectTask).toHaveBeenCalledWith("older-b");
    await act(async () => resolveFirst(conversation("a")));
    expect(select).toHaveBeenCalledTimes(1);
    act(() => hook.result.current.openBackgroundTaskLocation("a", null));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(3));
    hook.rerender({ scope: "project:second-manual-selection" });
    await act(async () => rejectFirst(new Error("late offline")));
    expect(errors).toEqual([]);
    act(() => hook.result.current.openBackgroundTaskLocation("a", null));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(4));
    hook.rerender({ scope: "project:manual-selection" });
    await act(async () => resolveFirst(conversation("a")));
    expect(select).toHaveBeenCalledTimes(1);
  } finally {
    hook.unmount(); queryClient.clear();
  }
});
