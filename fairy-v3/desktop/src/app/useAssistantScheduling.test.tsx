import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, expect, it, vi } from "vitest";

import type { WorkspaceClient } from "./workspaceTypes";
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
    modelSelection: null, allConversations: [], allTasks: [],
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
