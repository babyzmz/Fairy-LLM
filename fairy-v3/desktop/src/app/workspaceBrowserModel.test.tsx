import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";

import type { WorkspaceClient } from "./workspaceTypes";
import { useWorkspaceBrowser } from "./workspaceBrowserModel";

const conversationId = "019f6b2b-8000-7000-8000-000000000001";
const firstTaskId = "019f6b2b-8000-7000-8000-000000000002";
const secondTaskId = "019f6b2b-8000-7000-8000-000000000003";
const sessionId = "019f6b2b-8000-7000-8000-000000000004";
const tabId = "019f6b2b-8000-7000-8000-000000000005";

describe("useWorkspaceBrowser", () => {
  it("isolates session queries by Task and captures only while the surface is visible", async () => {
    const list = vi.fn(async (input: object) => ({ items: [session(input)] }));
    const getSnapshot = vi.fn(async () => ({
      session_id: sessionId,
      tab_id: tabId,
      page_revision: 1,
      url: "https://example.org/",
      title: "Example",
      aria_snapshot: "- document",
      viewport_width: 1365,
      viewport_height: 768,
      screenshot_data_url: "data:image/jpeg;base64,AA==",
      captured_at: "2026-07-21T00:00:00Z",
    }));
    const client = {
      browser: {
        health: vi.fn(async () => ({
          available: true,
          browser_name: "Microsoft Edge",
          browser_version: null,
          error_code: null,
          diagnostic: null,
        })),
        sessions: { list },
        snapshots: { get: getSnapshot },
      },
    } as unknown as WorkspaceClient;
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: PropsWithChildren) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const hook = renderHook(
      ({ taskId }: { taskId: string }) => useWorkspaceBrowser({
        client,
        enabled: true,
        conversationId,
        projectId: null,
        taskId,
        runAction: (operation) => operation(),
      }),
      { initialProps: { taskId: firstTaskId }, wrapper },
    );

    await waitFor(() => expect(list).toHaveBeenCalledWith({
      conversation_id: conversationId,
      task_id: firstTaskId,
      exact_task_scope: true,
    }));
    expect(getSnapshot).not.toHaveBeenCalled();

    act(() => hook.result.current.actions.setBrowserSurfaceActive(true));
    await waitFor(() => expect(getSnapshot).toHaveBeenCalledWith(sessionId, tabId));

    hook.rerender({ taskId: secondTaskId });
    await waitFor(() => expect(list).toHaveBeenCalledWith({
      conversation_id: conversationId,
      task_id: secondTaskId,
      exact_task_scope: true,
    }));
    hook.unmount();
    queryClient.clear();
  });
});

function session(input: object) {
  const taskId = (input as { task_id: string }).task_id;
  return {
    id: sessionId,
    project_id: null,
    conversation_id: conversationId,
    task_id: taskId,
    execution_target: "local" as const,
    profile_kind: "persistent" as const,
    status: "active" as const,
    active_tab_id: tabId,
    tabs: [{
      id: tabId,
      session_id: sessionId,
      title: "Example",
      url: "https://example.org/",
      active: true,
      loading: false,
      revision: 1,
    }],
    revision: 1,
    created_at: "2026-07-21T00:00:00Z",
    updated_at: "2026-07-21T00:00:00Z",
    error_code: null,
    public_error: null,
  };
}
