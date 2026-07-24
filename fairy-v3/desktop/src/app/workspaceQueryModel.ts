import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef } from "react";

import type { EventEnvelope } from "../core/client";
import type { WorkspaceClient, WorkspaceModel } from "./workspaceTypes";
import { collectCursorPages } from "./workspaceHistoryActions";
import {
  coreStartupRetryDelay,
  permissionQueryKey,
  shouldRetryCoreStartup,
  workspaceKey,
} from "./workspaceModelUtils";
import {
  addWorkspaceEventInvalidation,
  addWorkspaceInvalidation,
  createWorkspaceInvalidationBatch,
  workspaceQueryMatchesInvalidation,
} from "./workspaceQueryInvalidation";
import type { WorkspaceInvalidationDomain } from "./workspaceQueryInvalidation";

export const messageCacheStaleTime = 30_000;
const eventInvalidationWindow = 50;

export function useWorkspaceBaseQueries(client: WorkspaceClient) {
  const healthQuery = useQuery({
    queryKey: [...workspaceKey, "health"],
    queryFn: () => client.health(),
    retry: shouldRetryCoreStartup,
    retryDelay: coreStartupRetryDelay,
    refetchOnWindowFocus: false,
  });
  const permissionsQuery = useQuery({
    queryKey: permissionQueryKey,
    queryFn: () => client.permissions.get(),
    enabled: healthQuery.isSuccess,
    retry: false,
  });
  const projectsQuery = useQuery({
    queryKey: [...workspaceKey, "projects"],
    queryFn: () => collectCursorPages((cursor) => client.projects.list({ limit: 100, cursor })),
    enabled: healthQuery.isSuccess,
    retry: false,
  });
  const conversationsQuery = useQuery({
    queryKey: [...workspaceKey, "conversations"],
    queryFn: () => collectCursorPages((cursor) => client.conversations.list({ limit: 100, cursor })),
    enabled: healthQuery.isSuccess,
    retry: false,
  });
  const providersQuery = useQuery({
    queryKey: [...workspaceKey, "providers"],
    queryFn: () => client.providers.list(),
    enabled: healthQuery.isSuccess,
    retry: false,
  });
  const providerHealthQuery = useQuery({
    queryKey: [...workspaceKey, "provider-health"],
    queryFn: () => client.providers.health(),
    enabled: healthQuery.isSuccess,
    retry: false,
  });
  return {
    healthQuery,
    permissionsQuery,
    projectsQuery,
    conversationsQuery,
    providersQuery,
    providerHealthQuery,
  };
}

export function useConversationPrefetch(client: WorkspaceClient) {
  const queryClient = useQueryClient();
  return useCallback<WorkspaceModel["prefetchConversation"]>(
    async (conversation) => {
      await queryClient.prefetchQuery({
        queryKey: [
          ...workspaceKey,
          conversation.project_id === null ? "messages" : "project-messages",
          conversation.id,
        ],
        queryFn: () =>
          client.messages.list({
            conversation_id: conversation.id,
            limit: 100,
          }),
        staleTime: messageCacheStaleTime,
      });
    },
    [client, queryClient],
  );
}

export function useWorkspaceInvalidation() {
  const queryClient = useQueryClient();
  const invalidationBatchRef = useRef(createWorkspaceInvalidationBatch());
  const invalidationTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flushEventInvalidations = useCallback(async () => {
    const batch = invalidationBatchRef.current;
    invalidationBatchRef.current = createWorkspaceInvalidationBatch();
    invalidationTimerRef.current = null;
    if (batch.domains.size === 0) return;
    await queryClient.invalidateQueries({
      predicate: (query) => workspaceQueryMatchesInvalidation(query.queryKey, batch),
    });
  }, [queryClient]);

  const queueEventInvalidation = useCallback(
    (event: EventEnvelope) => {
      addWorkspaceEventInvalidation(invalidationBatchRef.current, event);
      if (
        invalidationBatchRef.current.domains.size === 0 ||
        invalidationTimerRef.current !== null
      ) {
        return;
      }
      invalidationTimerRef.current = setTimeout(() => {
        void flushEventInvalidations();
      }, eventInvalidationWindow);
    },
    [flushEventInvalidations],
  );

  useEffect(
    () => () => {
      if (invalidationTimerRef.current !== null) {
        clearTimeout(invalidationTimerRef.current);
        invalidationTimerRef.current = null;
      }
    },
    [],
  );

  const invalidateDomains = useCallback(async (
    domains: readonly WorkspaceInvalidationDomain[],
    scope: {
      conversationId?: string | null;
      projectId?: string | null;
      taskId?: string | null;
      turnId?: string | null;
    } = {},
  ) => {
    const batch = createWorkspaceInvalidationBatch();
    addWorkspaceInvalidation(batch, domains, scope);
    await queryClient.invalidateQueries({
      predicate: (query) => workspaceQueryMatchesInvalidation(query.queryKey, batch),
    });
  }, [queryClient]);

  const invalidateHistory = useCallback(
    () => invalidateDomains(["projects", "conversations"]),
    [invalidateDomains],
  );

  const invalidateAssistantScope = useCallback(
    async (conversationId: string | null, taskId: string | null) => {
      await invalidateDomains(
        ["messages", "tasks", "traces", "approvals", "workspace", "previews"],
        { conversationId, taskId },
      );
    },
    [invalidateDomains],
  );

  return {
    queueEventInvalidation,
    invalidateDomains,
    invalidateHistory,
    invalidateAssistantScope,
  };
}
