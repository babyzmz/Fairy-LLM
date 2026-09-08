import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { WorkspaceClient, WorkspaceModel } from "./workspaceTypes";
import { errorMessage, firstError, requireId, workspaceKey } from "./workspaceModelUtils";

type RunAction = <T>(operation: () => Promise<T>) => Promise<T>;

interface WorkspaceBrowserOptions {
  client: WorkspaceClient;
  enabled: boolean;
  conversationId: string | null;
  projectId: string | null;
  taskId: string | null;
  runAction: RunAction;
}

type BrowserActions = Pick<
  WorkspaceModel,
  | "startBrowser"
  | "stopBrowser"
  | "navigateBrowser"
  | "openBrowserTab"
  | "selectBrowserTab"
  | "closeBrowserTab"
  | "executeBrowserAction"
  | "refreshBrowser"
  | "setBrowserSurfaceActive"
>;

export function useWorkspaceBrowser({
  client,
  enabled,
  conversationId,
  projectId,
  taskId,
  runAction,
}: WorkspaceBrowserOptions) {
  const [surfaceActive, setSurfaceActive] = useState(false);
  const setBrowserSurfaceActive = useCallback((active: boolean) => {
    setSurfaceActive(active);
  }, []);
  const healthQuery = useQuery({
    queryKey: [...workspaceKey, "browser-health"],
    queryFn: () => client.browser.health(),
    enabled,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const sessionsQuery = useQuery({
    queryKey: [...workspaceKey, "browser-sessions", conversationId, taskId],
    queryFn: () => client.browser.sessions.list({
      conversation_id: conversationId,
      task_id: taskId,
      exact_task_scope: true,
    }),
    enabled: enabled && conversationId !== null,
    retry: false,
  });
  const session = sessionsQuery.data?.items.at(0) ?? null;
  const activeTab = session?.tabs.find((tab) => tab.id === session.active_tab_id) ?? null;
  const snapshotQuery = useQuery({
    queryKey: [...workspaceKey, "browser-snapshot", session?.id, activeTab?.id, activeTab?.revision],
    queryFn: () => client.browser.snapshots.get(requireId(session?.id), requireId(activeTab?.id)),
    enabled: surfaceActive && session?.status === "active" && activeTab !== null,
    retry: false,
    refetchInterval: session?.status === "active" ? 750 : false,
  });
  const refetchHealth = healthQuery.refetch;
  const refetchSessions = sessionsQuery.refetch;
  useEffect(() => {
    if (snapshotQuery.errorUpdatedAt === 0) return;
    void refetchHealth();
    void refetchSessions();
  }, [refetchHealth, refetchSessions, snapshotQuery.errorUpdatedAt]);
  const queryError = firstError(healthQuery.error, sessionsQuery.error, snapshotQuery.error);

  const actions = useMemo<BrowserActions>(() => {
    const commit = async <T>(operation: () => Promise<T>): Promise<T> => {
      const result = await runAction(operation);
      await refetchSessions();
      return result;
    };
    const startNew = (initialUrl?: string) => {
      if (conversationId === null) throw new Error("Conversation is unavailable");
      return client.browser.sessions.start({
        project_id: projectId,
        conversation_id: conversationId,
        task_id: taskId,
        execution_target: "local",
        profile_kind: "persistent",
        initial_url: initialUrl ?? null,
        idempotency_key: `desktop:browser:start:${crypto.randomUUID()}`,
      });
    };
    const resume = () => {
      if (session === null) throw new Error("Browser session is unavailable");
      return client.browser.sessions.resume(session.id);
    };
    const pageRevision = (targetSession: NonNullable<typeof session>, tab: NonNullable<typeof activeTab>) => {
      const snapshot = snapshotQuery.data;
      // A resumed session owns a new page lifecycle; never reuse the pre-resume snapshot.
      const matches = session?.status === "active"
        && targetSession.id === session.id
        && snapshot?.session_id === targetSession.id
        && snapshot.tab_id === tab.id;
      return matches ? Math.max(tab.revision, snapshot.page_revision) : tab.revision;
    };
    const navigateSession = async (targetSession: NonNullable<typeof session>, url: string) => {
      const tab = targetSession.tabs.find((item) => item.id === targetSession.active_tab_id) ?? null;
      if (tab === null) throw new Error("Browser session has no active tab");
      await client.browser.actions.execute({
        session_id: targetSession.id,
        tab_id: tab.id,
        kind: "navigate",
        value: url,
        expected_page_revision: pageRevision(targetSession, tab),
        idempotency_key: `desktop:browser:navigate:${crypto.randomUUID()}`,
      });
    };
    const needsResume = session?.status === "interrupted" || session?.status === "suspended";

    return {
      async startBrowser(initialUrl?: string) {
        if (!needsResume) {
          await commit(() => startNew(initialUrl));
          return;
        }
        const resumed = await commit(resume);
        if (initialUrl !== undefined) await commit(() => navigateSession(resumed, initialUrl));
      },
      async stopBrowser() {
        if (session !== null) await commit(() => client.browser.sessions.stop(session.id));
      },
      async navigateBrowser(url: string) {
        const targetSession = needsResume
          ? await commit(resume)
          : session ?? await commit(() => startNew(url));
        if (session === null && !needsResume) return;
        await commit(() => navigateSession(targetSession, url));
      },
      async openBrowserTab(url = "about:blank") {
        const targetSession = needsResume
          ? await commit(resume)
          : session ?? await commit(() => startNew(url));
        if (session === null && !needsResume) return;
        await commit(() => client.browser.tabs.open(targetSession.id, url));
      },
      async selectBrowserTab(tabId: string) {
        if (session === null || session.active_tab_id === tabId) return;
        await commit(() => client.browser.tabs.select(session.id, tabId));
      },
      async closeBrowserTab(tabId: string) {
        if (session !== null) await commit(() => client.browser.tabs.close(session.id, tabId));
      },
      async executeBrowserAction(input) {
        if (session === null || activeTab === null || session.status !== "active") {
          throw new Error("Browser session is unavailable");
        }
        await commit(() => client.browser.actions.execute({
          ...input,
          session_id: session.id,
          tab_id: activeTab.id,
          expected_page_revision: input.expected_page_revision
            ?? pageRevision(session, activeTab),
          idempotency_key: `desktop:browser:${input.kind}:${crypto.randomUUID()}`,
        }));
      },
      async refreshBrowser() {
        await snapshotQuery.refetch();
      },
      setBrowserSurfaceActive,
    };
  }, [activeTab, client.browser, conversationId, projectId, refetchSessions, runAction, session, setBrowserSurfaceActive, snapshotQuery, taskId]);

  return {
    health: healthQuery.data ?? null,
    session,
    snapshot: surfaceActive && session?.status === "active" ? snapshotQuery.data ?? null : null,
    loading: healthQuery.isPending
      || sessionsQuery.isPending
      || (surfaceActive && session?.status === "active" && snapshotQuery.isFetching),
    error: queryError === null ? null : errorMessage(queryError),
    actions,
  };
}
