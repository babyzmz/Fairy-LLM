import { useInfiniteQuery, useQueries } from "@tanstack/react-query";

import type { Task } from "../core/client";
import type { WorkspaceClient } from "./workspaceTypes";
import { requireId, workspaceKey } from "./workspaceModelUtils";

export type HistoryTaskScope = { conversationId: string } | { projectId: string };

export function useWorkspaceTasks(
  client: WorkspaceClient,
  enabled: boolean,
  conversationId: string | null,
  requiredIds: (string | null)[],
) {
  const pages = useInfiniteQuery({
    queryKey: [...workspaceKey, "tasks", conversationId],
    queryFn: ({ pageParam }) => client.tasks.list({
      conversation_id: requireId(conversationId), limit: 100, cursor: pageParam,
    }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage, _pages, _lastParam, pageParams) =>
      lastPage.next_cursor !== null && !pageParams.includes(lastPage.next_cursor)
        ? lastPage.next_cursor : undefined,
    enabled: enabled && conversationId !== null,
    retry: false,
    staleTime: 30_000,
  });
  const items = pages.data?.pages.flatMap((page) => page.items)
    .filter((task) => task.conversation_id === conversationId) ?? [];
  const missingIds = [...new Set(requiredIds.filter((id): id is string =>
    id !== null && !items.some((task) => task.id === id)))];
  const required = useQueries({ queries: missingIds.map((id) => ({
    queryKey: [...workspaceKey, "task-detail", conversationId, id],
    queryFn: () => client.tasks.get(id),
    enabled: enabled && conversationId !== null && pages.isSuccess,
    retry: false,
    staleTime: 30_000,
  })) });
  const byId = new Map<string, Task>(items.map((task) => [task.id, task]));
  for (const query of required) {
    if (query.data?.conversation_id === conversationId && !byId.has(query.data.id)) {
      byId.set(query.data.id, query.data);
    }
  }
  return {
    items: [...byId.values()],
    error: pages.error ?? required.find((query) => query.error !== null)?.error ?? null,
    loading: pages.isPending && pages.isEnabled,
    hasMore: pages.hasNextPage,
    loadingMore: pages.isFetchingNextPage,
    loadMore: async () => { await pages.fetchNextPage(); },
  };
}

export async function countHistoryActiveTasks(client: WorkspaceClient, scope: HistoryTaskScope): Promise<number> {
  let cursor: string | null = null;
  let count = 0;
  const seen = new Set<string>();
  do {
    const page = await client.tasks.list({
      ...("conversationId" in scope ? { conversation_id: scope.conversationId } : { project_id: scope.projectId }),
      limit: 100, cursor,
    });
    count += page.items.filter((task) => (
      "conversationId" in scope ? task.conversation_id === scope.conversationId : task.project_id === scope.projectId
    ) && !["ready", "accepted", "rejected", "archived", "failed"].includes(task.status)).length;
    cursor = page.next_cursor;
    if (cursor !== null) {
      if (seen.has(cursor)) throw new Error("Task pagination did not advance; retry the check.");
      seen.add(cursor);
    }
  } while (cursor !== null);
  return count;
}
