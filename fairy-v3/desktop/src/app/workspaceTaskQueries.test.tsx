import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, expect, it, vi } from "vitest";

import type { Task } from "../core/client";
import type { WorkspaceClient } from "./workspaceTypes";
import { countHistoryActiveTasks, useWorkspaceTasks } from "./workspaceTaskQueries";

afterEach(cleanup);
const task = (id: string, conversationId: string, status = "executing") => ({
  id, conversation_id: conversationId, project_id: "project", status,
}) as Task;

it("isolates late task pages and pinned detail across A to B to A", async () => {
  let resolveA!: (value: { items: Task[]; next_cursor: null }) => void;
  const list = vi.fn<WorkspaceClient["tasks"]["list"]>(async (input) => input?.conversation_id === "a"
    ? new Promise((resolve) => { resolveA = resolve; })
    : { items: [task("b", "b")], next_cursor: null });
  const client = { tasks: { list, get: vi.fn(async () => task("old-a", "a")) } } as unknown as WorkspaceClient;
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const wrapper = ({ children }: PropsWithChildren) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  const hook = renderHook(({ conversationId }) => useWorkspaceTasks(client, true, conversationId, ["old-a"]), {
    wrapper, initialProps: { conversationId: "a" },
  });
  try {
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1));
    hook.rerender({ conversationId: "b" });
    await waitFor(() => expect(hook.result.current.items.map((item) => item.id)).toEqual(["b"]));
    await act(async () => resolveA({ items: [task("a", "a")], next_cursor: null }));
    expect(hook.result.current.items.map((item) => item.id)).toEqual(["b"]);
    hook.rerender({ conversationId: "a" });
    await waitFor(() => expect(hook.result.current.items.map((item) => item.id)).toEqual(["a", "old-a"]));
    expect(list).toHaveBeenCalledTimes(2);
  } finally {
    hook.unmount(); queryClient.clear();
  }
});

it("counts all scoped pages on demand and refuses incomplete or looping reads", async () => {
  const list = vi.fn<WorkspaceClient["tasks"]["list"]>(async (input) => ({
    items: input?.cursor
      ? [task("a2", "a"), task("foreign", "b")]
      : [task("a1", "a"), task("done", "a", "ready")],
    next_cursor: input?.cursor ? null : "next",
  }));
  const client = { tasks: { list } } as unknown as WorkspaceClient;
  expect(await countHistoryActiveTasks(client, { conversationId: "a" })).toBe(2);
  expect(list.mock.calls.every(([input]) => input?.conversation_id === "a")).toBe(true);
  list.mockRejectedValueOnce(new Error("offline"));
  await expect(countHistoryActiveTasks(client, { projectId: "project" })).rejects.toThrow("offline");
  list.mockResolvedValue({ items: [], next_cursor: "stuck" });
  await expect(countHistoryActiveTasks(client, { conversationId: "a" })).rejects.toThrow("did not advance");
});
