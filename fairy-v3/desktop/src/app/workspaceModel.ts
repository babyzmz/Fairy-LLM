import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";

import type {
  Approval,
  CapabilityManifest,
  Conversation,
  CoreClient,
  EventEnvelope,
  PreviewContext,
  Project,
  RuntimeHealth,
  Task,
  Version,
} from "../core/client";

export interface WorkspaceClient {
  health: CoreClient["health"];
  projects: Pick<CoreClient["projects"], "list" | "create" | "import">;
  conversations: Pick<CoreClient["conversations"], "list">;
  tasks: Pick<CoreClient["tasks"], "list" | "create">;
  approvals: Pick<CoreClient["approvals"], "list" | "decide">;
  versions: Pick<CoreClient["versions"], "list" | "accept" | "discard">;
  runtimes: Pick<CoreClient["runtimes"], "health">;
  previews: Pick<CoreClient["previews"], "resolve" | "start" | "stop">;
  capabilities: Pick<CoreClient["capabilities"], "get">;
  events: Pick<CoreClient["events"], "subscribe">;
}

export interface WorkspaceModel {
  state: "loading" | "offline" | "empty" | "ready";
  statusLabel: string;
  errorMessage: string | null;
  actionError: string | null;
  isActing: boolean;
  projects: Project[];
  conversations: Conversation[];
  tasks: Task[];
  versions: Version[];
  approvals: Approval[];
  events: EventEnvelope[];
  selectedProject: Project | null;
  selectedConversation: Conversation | null;
  selectedTask: Task | null;
  selectedVersion: Version | null;
  preview: PreviewContext | null;
  runtimeHealth: RuntimeHealth | null;
  capabilities: CapabilityManifest | null;
  selectProject(projectId: string): void;
  selectConversation(conversationId: string): void;
  selectTask(taskId: string): void;
  createProject(name: string): Promise<void>;
  importProject(name: string, sourcePath: string): Promise<void>;
  createTask(userRequest: string): Promise<void>;
  decideApproval(approvalId: string, approved: boolean): Promise<void>;
  startPreview(): Promise<void>;
  stopPreview(): Promise<void>;
  acceptVersion(): Promise<void>;
  discardVersion(): Promise<void>;
}

const workspaceKey = ["workspace"] as const;

export function useWorkspaceModel(client: WorkspaceClient): WorkspaceModel {
  const queryClient = useQueryClient();
  const [projectSelection, setProjectSelection] = usePersistedSelection(
    "fairy.workspace.project",
  );
  const [conversationSelection, setConversationSelection] = usePersistedSelection(
    "fairy.workspace.conversation",
  );
  const [taskSelection, setTaskSelection] = usePersistedSelection(
    "fairy.workspace.task",
  );
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [actionError, setActionError] = useState<string | null>(null);
  const [isActing, setIsActing] = useState(false);

  const healthQuery = useQuery({
    queryKey: [...workspaceKey, "health"],
    queryFn: () => client.health(),
    retry: false,
    refetchOnWindowFocus: false,
  });
  const projectsQuery = useQuery({
    queryKey: [...workspaceKey, "projects"],
    queryFn: () => client.projects.list({ limit: 100 }),
    enabled: healthQuery.isSuccess,
    retry: false,
  });
  const projects = projectsQuery.data?.items ?? [];
  const selectedProject = selectedItem(projects, projectSelection);

  const conversationsQuery = useQuery({
    queryKey: [...workspaceKey, "conversations", selectedProject?.id],
    queryFn: () =>
      client.conversations.list({ project_id: requireId(selectedProject?.id) }),
    enabled: selectedProject !== null,
    retry: false,
  });
  const conversations = conversationsQuery.data?.items ?? [];
  const selectedConversation = selectedItem(conversations, conversationSelection);

  const tasksQuery = useQuery({
    queryKey: [...workspaceKey, "tasks", selectedConversation?.id],
    queryFn: () =>
      client.tasks.list({ conversation_id: requireId(selectedConversation?.id) }),
    enabled: selectedConversation !== null,
    retry: false,
  });
  const tasks = tasksQuery.data?.items ?? [];
  const selectedTask = selectedItem(tasks, taskSelection);

  const versionsQuery = useQuery({
    queryKey: [...workspaceKey, "versions", selectedProject?.id],
    queryFn: () => client.versions.list({ project_id: requireId(selectedProject?.id) }),
    enabled: selectedProject !== null,
    retry: false,
  });
  const versions = versionsQuery.data?.items ?? [];
  const selectedVersion =
    versions.find((version) => version.id === selectedTask?.target_version_id) ??
    versions.find((version) => version.id === selectedProject?.active_version_id) ??
    versions.at(-1) ??
    null;

  const approvalsQuery = useQuery({
    queryKey: [...workspaceKey, "approvals", selectedTask?.id],
    queryFn: () => client.approvals.list({ task_id: requireId(selectedTask?.id) }),
    enabled: selectedTask !== null,
    retry: false,
  });
  const approvals = approvalsQuery.data?.items ?? [];

  const previewQuery = useQuery({
    queryKey: [...workspaceKey, "preview", selectedConversation?.id],
    queryFn: () =>
      client.previews.resolve({
        conversation_id: requireId(selectedConversation?.id),
      }),
    enabled: selectedConversation !== null,
    retry: false,
  });
  const runtimeHealthQuery = useQuery({
    queryKey: [...workspaceKey, "runtime-health", selectedTask?.id],
    queryFn: () => client.runtimes.health(requireId(selectedTask?.id)),
    enabled: selectedTask !== null,
    retry: false,
  });
  const capabilitiesQuery = useQuery({
    queryKey: [...workspaceKey, "capabilities"],
    queryFn: () =>
      client.capabilities.get({
        profile: "standard",
        sandbox_healthy: false,
        overrides: {},
      }),
    enabled: healthQuery.isSuccess,
    retry: false,
  });

  useEffect(() => {
    if (!healthQuery.isSuccess) return;
    const controller = new AbortController();
    void (async () => {
      try {
        for await (const event of client.events.subscribe(0, {
          signal: controller.signal,
        })) {
          if (event.visibility !== "user") continue;
          setEvents((current) => appendEvent(current, event));
          void queryClient.invalidateQueries({
            queryKey: workspaceKey,
            predicate: (query) => query.queryKey[1] !== "health",
          });
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setActionError(errorMessage(error));
        }
      }
    })();
    return () => controller.abort();
  }, [client, healthQuery.isSuccess, queryClient]);

  const invalidateWorkspace = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: workspaceKey });
  }, [queryClient]);

  const runAction = useCallback(
    async <T,>(operation: () => Promise<T>): Promise<T> => {
      setIsActing(true);
      setActionError(null);
      try {
        const result = await operation();
        await invalidateWorkspace();
        return result;
      } catch (error) {
        setActionError(errorMessage(error));
        throw error;
      } finally {
        setIsActing(false);
      }
    },
    [invalidateWorkspace],
  );

  const actions = useMemo(
    () => ({
      async createProject(name: string) {
        const result = await runAction(() =>
          client.projects.create({ name, residency: "local_only" }),
        );
        setProjectSelection(result.project.id);
        setConversationSelection(null);
        setTaskSelection(null);
      },
      async importProject(name: string, sourcePath: string) {
        const result = await runAction(() =>
          client.projects.import({
            name,
            residency: "local_only",
            source_path: sourcePath,
          }),
        );
        setProjectSelection(result.project.id);
        setConversationSelection(null);
        setTaskSelection(null);
      },
      async createTask(userRequest: string) {
        if (selectedConversation === null) throw new Error("Conversation is unavailable");
        const result = await runAction(() =>
          client.tasks.create({
            conversation_id: selectedConversation.id,
            user_request: userRequest,
            operation_mode: "continue_current_chat_draft",
            execution_target: "local",
            idempotency_key: `desktop:task:${crypto.randomUUID()}`,
          }),
        );
        setTaskSelection(result.task.id);
      },
      async decideApproval(approvalId: string, approved: boolean) {
        await runAction(() =>
          client.approvals.decide({
            approval_id: approvalId,
            approved,
            decided_by: "desktop-user",
          }),
        );
      },
      async startPreview() {
        if (selectedTask === null) throw new Error("Task is unavailable");
        await runAction(() =>
          client.previews.start({
            task_id: selectedTask.id,
            idempotency_key: `desktop:preview:${selectedTask.id}`,
          }),
        );
      },
      async stopPreview() {
        const activePreview = previewQuery.data?.preview;
        if (activePreview === undefined || activePreview === null) return;
        await runAction(() =>
          client.previews.stop({
            preview_id: activePreview.id,
            idempotency_key: `desktop:preview:${activePreview.id}:stop`,
          }),
        );
      },
      async acceptVersion() {
        if (selectedTask === null || selectedProject === null) {
          throw new Error("Version is unavailable");
        }
        await runAction(() =>
          client.versions.accept({
            task_id: selectedTask.id,
            expected_project_revision: selectedProject.revision,
            user_confirmed: true,
          }),
        );
      },
      async discardVersion() {
        if (selectedTask === null) throw new Error("Task is unavailable");
        const activePreview = previewQuery.data?.preview;
        await runAction(async () => {
          if (
            activePreview !== undefined &&
            activePreview !== null &&
            !["stopped", "failed"].includes(activePreview.status)
          ) {
            await client.previews.stop({
              preview_id: activePreview.id,
              idempotency_key: `desktop:preview:${activePreview.id}:discard-stop`,
            });
          }
          await client.versions.discard(selectedTask.id);
        });
      },
    }),
    [
      client,
      previewQuery.data?.preview,
      runAction,
      selectedConversation,
      selectedProject,
      selectedTask,
      setConversationSelection,
      setProjectSelection,
      setTaskSelection,
    ],
  );

  const queryError = firstError(
    projectsQuery.error,
    conversationsQuery.error,
    tasksQuery.error,
    versionsQuery.error,
    approvalsQuery.error,
    previewQuery.error,
    runtimeHealthQuery.error,
    capabilitiesQuery.error,
  );
  const isLoading =
    healthQuery.isPending ||
    (healthQuery.isSuccess && projectsQuery.isPending) ||
    (selectedProject !== null && conversationsQuery.isPending) ||
    (selectedConversation !== null && tasksQuery.isPending);
  const state = healthQuery.isError
    ? "offline"
    : isLoading
      ? "loading"
      : projects.length === 0
        ? "empty"
        : "ready";

  return {
    state,
    statusLabel:
      state === "offline" ? "Core offline" : state === "loading" ? "Core starting" : "Core ready",
    errorMessage: queryError === null ? null : errorMessage(queryError),
    actionError,
    isActing,
    projects,
    conversations,
    tasks,
    versions,
    approvals,
    events: events.filter((event) => event.task_id === selectedTask?.id),
    selectedProject,
    selectedConversation,
    selectedTask,
    selectedVersion,
    preview: previewQuery.data ?? null,
    runtimeHealth: runtimeHealthQuery.data ?? null,
    capabilities: capabilitiesQuery.data ?? null,
    selectProject: setProjectSelection,
    selectConversation: setConversationSelection,
    selectTask: setTaskSelection,
    ...actions,
  };
}

function selectedItem<T extends { id: string }>(items: T[], selectedId: string | null): T | null {
  return items.find((item) => item.id === selectedId) ?? items.at(-1) ?? null;
}

function requireId(value: string | undefined): string {
  if (value === undefined) throw new Error("Workspace scope is unavailable");
  return value;
}

function appendEvent(events: EventEnvelope[], incoming: EventEnvelope): EventEnvelope[] {
  if (events.some((event) => event.id === incoming.id)) return events;
  return [...events, incoming].sort((left, right) => left.cursor - right.cursor).slice(-300);
}

function firstError(...errors: (Error | null)[]): Error | null {
  return errors.find((error) => error !== null) ?? null;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Workspace request failed";
}

function usePersistedSelection(
  key: string,
): [string | null, (value: string | null) => void] {
  const [value, setValue] = useState<string | null>(() => {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  });
  const update = useCallback(
    (next: string | null) => {
      setValue(next);
      try {
        if (next === null) window.localStorage.removeItem(key);
        else window.localStorage.setItem(key, next);
      } catch {
        // Selection persistence is optional; Core remains authoritative.
      }
    },
    [key],
  );
  return [value, update];
}
