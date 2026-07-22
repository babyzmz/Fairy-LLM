import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";

import type { WorkspaceClient } from "./workspaceTypes";
import { useWorkspaceKnowledge } from "./workspaceKnowledgeModel";

const projectId = "019f87ef-8000-7000-8000-000000000001";

describe("useWorkspaceKnowledge", () => {
  it("does not request persisted project knowledge while an ordinary chat is active", async () => {
    const overview = vi.fn(async () => ({
      project_id: projectId,
      source_version_id: null,
      source_revision: 1,
      watermark: "a".repeat(64),
      file_count: 0,
      note_count: 0,
      conversation_count: 0,
      relation_count: 0,
      obsidian_connected: false,
      obsidian_health: "not_connected",
    }));
    const listItems = vi.fn(async () => ({
      items: [],
      source_revision: 1,
      watermark: "a".repeat(64),
    }));
    const graph = vi.fn(async () => ({
      project_id: projectId,
      source_version_id: null,
      source_revision: 1,
      watermark: "a".repeat(64),
      nodes: [],
      edges: [],
    }));
    const listSources = vi.fn(async () => ({ items: [] }));
    const client = {
      knowledge: { overview, listItems, graph },
      obsidian: {
        health: vi.fn(async () => ({
          desktop_installed: false,
          cli_available: false,
          minimum_supported_version: "1.12.7",
          detected_version: null,
          status: "unavailable",
          public_summary: "Obsidian is unavailable",
        })),
        listSources,
        listItems: vi.fn(),
      },
    } as unknown as WorkspaceClient;
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: PropsWithChildren) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const hook = renderHook(
      ({ mode }: { mode: "chat" | "project" }) => useWorkspaceKnowledge({
        client,
        enabled: true,
        mode,
        projectId,
      }),
      { initialProps: { mode: "chat" }, wrapper },
    );

    await waitFor(() => expect(client.obsidian.health).toHaveBeenCalledOnce());
    expect(overview).not.toHaveBeenCalled();
    expect(listItems).not.toHaveBeenCalled();
    expect(graph).not.toHaveBeenCalled();
    expect(listSources).not.toHaveBeenCalled();
    expect(hook.result.current.knowledgeLoading).toBe(false);
    expect(hook.result.current.obsidianLoading).toBe(false);

    hook.rerender({ mode: "project" });
    await waitFor(() => expect(overview).toHaveBeenCalledWith(projectId));
    await waitFor(() => expect(graph).toHaveBeenCalledWith(projectId));
    await waitFor(() => expect(listSources).toHaveBeenCalledWith(projectId));
    hook.unmount();
    queryClient.clear();
  });
});
