import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";

import type { MediaGenerationJobPage } from "../core/contracts";
import { useTaskMediaJobs } from "./useTaskMediaJobs";

describe("useTaskMediaJobs", () => {
  it("does not project a delayed Conversation A response after switching to B", async () => {
    let resolveA: (page: MediaGenerationJobPage) => void = () => {
      throw new Error("resolver not initialized");
    };
    const delayedA = new Promise<MediaGenerationJobPage>((resolve) => { resolveA = resolve; });
    const list = vi.fn((taskId: string) => taskId === TASK_A ? delayedA : Promise.resolve({ items: [] }));
    const { result, rerender } = renderHook(
      ({ conversationId, taskId }) =>
        useTaskMediaJobs({ media: { jobs: { list } } }, { conversationId, taskId }),
      {
        initialProps: { conversationId: CONVERSATION_A, taskId: TASK_A },
        wrapper: queryWrapper(),
      },
    );

    rerender({ conversationId: CONVERSATION_B, taskId: TASK_B });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.items).toEqual([]);

    await act(async () => resolveA({ items: [] }));
    expect(result.current.data?.items).toEqual([]);
    expect(list).toHaveBeenCalledWith(TASK_A);
    expect(list).toHaveBeenCalledWith(TASK_B);
  });
});

const CONVERSATION_A = "019f566f-f8b4-7000-8000-0000000000a1";
const CONVERSATION_B = "019f566f-f8b4-7000-8000-0000000000b1";
const TASK_A = "019f566f-f8b4-7000-8000-0000000000a2";
const TASK_B = "019f566f-f8b4-7000-8000-0000000000b2";

function queryWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  return function Wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}
