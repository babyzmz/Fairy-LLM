import { useQueries, useQuery } from "@tanstack/react-query";

import type { WorkspaceClient, WorkspaceMode } from "./workspaceTypes";
import {
  errorMessage,
  firstError,
  requireId,
  workspaceKey,
} from "./workspaceModelUtils";

interface WorkspaceKnowledgeInput {
  client: WorkspaceClient;
  enabled: boolean;
  mode: WorkspaceMode;
  projectId: string | null;
}

export function useWorkspaceKnowledge({ client, enabled, mode, projectId }: WorkspaceKnowledgeInput) {
  const scopedProjectId = mode === "project" ? projectId : null;
  const projectEnabled = enabled && scopedProjectId !== null;
  const obsidianHealthQuery = useQuery({
    queryKey: [...workspaceKey, "obsidian", "health"],
    queryFn: () => client.obsidian.health(),
    enabled,
    retry: false,
  });
  const knowledgeOverviewQuery = useQuery({
    queryKey: [...workspaceKey, "knowledge", "overview", scopedProjectId],
    queryFn: () => client.knowledge.overview(requireId(scopedProjectId)),
    enabled: projectEnabled,
    retry: false,
  });
  const graphWatermark = knowledgeOverviewQuery.data?.watermark ?? "unresolved";
  const knowledgeItemsQuery = useQuery({
    queryKey: [...workspaceKey, "knowledge", "items", scopedProjectId, graphWatermark],
    queryFn: () => client.knowledge.listItems({ project_id: requireId(scopedProjectId), limit: 500 }),
    enabled: projectEnabled,
    retry: false,
  });
  const knowledgeGraphQuery = useQuery({
    queryKey: [...workspaceKey, "knowledge", "graph", scopedProjectId, graphWatermark],
    queryFn: () => client.knowledge.graph(requireId(scopedProjectId)),
    enabled: projectEnabled,
    retry: false,
  });
  const obsidianSourcesQuery = useQuery({
    queryKey: [...workspaceKey, "obsidian", "sources", scopedProjectId],
    queryFn: () => client.obsidian.listSources(requireId(scopedProjectId)),
    enabled: projectEnabled,
    retry: false,
  });
  const obsidianSources = obsidianSourcesQuery.data?.items ?? [];
  const obsidianItemQueries = useQueries({
    queries: obsidianSources.map((source) => ({
      queryKey: [
        ...workspaceKey,
        "obsidian",
        "items",
        scopedProjectId,
        source.id,
        source.revision,
      ],
      queryFn: () => client.obsidian.listItems(source.id),
      enabled: projectEnabled,
      retry: false,
    })),
  });
  const obsidianItems = obsidianItemQueries.flatMap((query) => query.data?.items ?? []);
  const obsidianSourceProjections = obsidianSources.map((source, index) => {
    const query = obsidianItemQueries[index];
    return {
      sourceId: source.id,
      sourceRevision: query?.data?.source_revision ?? null,
      itemCount: query?.data?.items.length ?? 0,
      loading: query?.isPending ?? false,
      error: query?.error === null || query?.error === undefined ? null : errorMessage(query.error),
    };
  });
  const obsidianError = obsidianSourcesQuery.error !== null
    ? errorMessage(obsidianSourcesQuery.error)
    : obsidianSourceProjections.find((projection) => projection.error !== null)?.error ?? null;

  return {
    knowledgeOverview: knowledgeOverviewQuery.data ?? null,
    knowledgeItems: knowledgeItemsQuery.data?.items ?? [],
    knowledgeGraph: knowledgeGraphQuery.data ?? null,
    knowledgeLoading:
      projectEnabled && (
        knowledgeOverviewQuery.isPending || knowledgeItemsQuery.isPending || knowledgeGraphQuery.isPending
      ),
    knowledgeError: firstError(
      knowledgeOverviewQuery.error,
      knowledgeItemsQuery.error,
      knowledgeGraphQuery.error,
    )?.message ?? null,
    obsidianHealth: obsidianHealthQuery.data ?? null,
    obsidianSources,
    obsidianItems,
    obsidianSourceProjections,
    obsidianLoading:
      projectEnabled && (
        obsidianSourcesQuery.isPending || obsidianSourceProjections.some((projection) => projection.loading)
      ),
    obsidianError,
  };
}
