import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type AssistantTurnClient, useAssistantTurn } from "../chat/useAssistantTurn";
import type {
  Approval,
  AssistantTurn,
  CapabilityManifest,
  Conversation,
  CoreClient,
  EventEnvelope,
  Message,
  PreviewContext,
  Project,
  ProviderHealth,
  ProviderProfile,
  RuntimeHealth,
  Task,
  Version,
} from "../core/client";

export type WorkspaceMode = "project" | "chat";
export type PermissionProfile = "observe" | "standard" | "autonomous";

export interface WorkspaceClient extends AssistantTurnClient {
  health: CoreClient["health"];
  projects: Pick<CoreClient["projects"], "list" | "create" | "import">;
  conversations: Pick<CoreClient["conversations"], "list" | "create">;
  tasks: Pick<CoreClient["tasks"], "list" | "create">;
  approvals: Pick<CoreClient["approvals"], "list" | "decide">;
  versions: Pick<CoreClient["versions"], "list" | "accept" | "discard">;
  runtimes: Pick<CoreClient["runtimes"], "health">;
  previews: Pick<CoreClient["previews"], "resolve" | "start" | "stop">;
  capabilities: Pick<CoreClient["capabilities"], "get">;
  providers: Pick<CoreClient["providers"], "list" | "health">;
  messages: Pick<CoreClient["messages"], "list">;
  voice: Pick<CoreClient["voice"], "transcribe" | "synthesize">;
  events: Pick<CoreClient["events"], "subscribe">;
}

export interface WorkspaceModel {
  state: "loading" | "offline" | "empty" | "ready";
  mode: WorkspaceMode;
  statusLabel: string;
  errorMessage: string | null;
  actionError: string | null;
  isActing: boolean;
  permissionProfile: PermissionProfile;
  developerMode: boolean;
  projects: Project[];
  conversations: Conversation[];
  chatConversations: Conversation[];
  tasks: Task[];
  versions: Version[];
  approvals: Approval[];
  events: EventEnvelope[];
  messages: Message[];
  providers: ProviderProfile[];
  providerHealth: ProviderHealth[];
  selectedProfileId: string | null;
  selectedProject: Project | null;
  selectedConversation: Conversation | null;
  selectedChatConversation: Conversation | null;
  selectedTask: Task | null;
  selectedVersion: Version | null;
  preview: PreviewContext | null;
  runtimeHealth: RuntimeHealth | null;
  capabilities: CapabilityManifest | null;
  chatTurn: AssistantTurn | null;
  chatStreamedText: string;
  chatBusy: boolean;
  chatError: string | null;
  projectTurn: AssistantTurn | null;
  projectBusy: boolean;
  projectError: string | null;
  setMode(mode: WorkspaceMode): void;
  setPermissionProfile(profile: PermissionProfile): void;
  setDeveloperMode(enabled: boolean): void;
  selectProfile(profileId: string): void;
  selectProject(projectId: string): void;
  selectConversation(conversationId: string): void;
  selectChatConversation(conversationId: string): void;
  selectTask(taskId: string): void;
  createProject(name: string): Promise<void>;
  importProject(name: string, sourcePath: string): Promise<void>;
  createChatConversation(): Promise<void>;
  createTask(userRequest: string): Promise<void>;
  sendChatMessage(value: string, files: File[]): Promise<void>;
  sendProjectMessage(value: string, files: File[]): Promise<void>;
  cancelChatTurn(): Promise<void>;
  retryChatTurn(): Promise<void>;
  cancelProjectTurn(): Promise<void>;
  decideApproval(approvalId: string, approved: boolean): Promise<void>;
  startPreview(): Promise<void>;
  stopPreview(): Promise<void>;
  acceptVersion(): Promise<void>;
  discardVersion(): Promise<void>;
}

const workspaceKey = ["workspace"] as const;
const terminalAssistantEvents = new Set([
  "assistant.turn.completed",
  "assistant.turn.cancelled",
  "assistant.turn.failed",
]);

export function useWorkspaceModel(client: WorkspaceClient): WorkspaceModel {
  const queryClient = useQueryClient();
  const [mode, setMode] = usePersistedEnum<WorkspaceMode>(
    "fairy.workspace.mode",
    "project",
    ["project", "chat"],
  );
  const [permissionProfile, setPermissionProfile] = usePersistedEnum<PermissionProfile>(
    "fairy.workspace.permission",
    "standard",
    ["observe", "standard", "autonomous"],
  );
  const [developerMode, setDeveloperMode] = usePersistedBoolean(
    "fairy.workspace.developer",
    false,
  );
  const [projectSelection, setProjectSelection] = usePersistedSelection(
    "fairy.workspace.project",
  );
  const [conversationSelection, setConversationSelection] = usePersistedSelection(
    "fairy.workspace.conversation",
  );
  const [chatConversationSelection, setChatConversationSelection] =
    usePersistedSelection("fairy.workspace.chat-conversation");
  const [taskSelection, setTaskSelection] = usePersistedSelection(
    "fairy.workspace.task",
  );
  const [profileSelection, setProfileSelection] = usePersistedSelection(
    "fairy.workspace.provider",
  );
  const [allEvents, setAllEvents] = useState<EventEnvelope[]>([]);
  const eventCursor = useRef(readEventCursor());
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
  const conversationsQuery = useQuery({
    queryKey: [...workspaceKey, "conversations"],
    queryFn: () => client.conversations.list({ limit: 100 }),
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

  const projects = projectsQuery.data?.items ?? [];
  const selectedProject = selectedItem(projects, projectSelection);
  const allConversations = conversationsQuery.data?.items ?? [];
  const conversations = allConversations.filter(
    (conversation) => conversation.project_id === selectedProject?.id,
  );
  const chatConversations = allConversations.filter(
    (conversation) =>
      conversation.project_id === null && conversation.workspace_type === "chat_scratch",
  );
  const selectedConversation = selectedItem(conversations, conversationSelection);
  const selectedChatConversation = selectedItem(
    chatConversations,
    chatConversationSelection,
  );
  const providers = providersQuery.data?.items ?? [];
  const providerHealth = providerHealthQuery.data?.items ?? [];
  const selectedProfile =
    providers.find((profile) => profile.id === profileSelection) ??
    providers.find(
      (profile) => profile.enabled && profile.capabilities.includes("text"),
    ) ??
    null;
  const selectedProfileId = selectedProfile?.id ?? null;

  const tasksQuery = useQuery({
    queryKey: [...workspaceKey, "tasks", selectedConversation?.id],
    queryFn: () =>
      client.tasks.list({ conversation_id: requireId(selectedConversation?.id) }),
    enabled: selectedConversation !== null,
    retry: false,
  });
  const tasks = tasksQuery.data?.items ?? [];
  const selectedTask = selectedItem(tasks, taskSelection);
  const messagesQuery = useQuery({
    queryKey: [...workspaceKey, "messages", selectedChatConversation?.id],
    queryFn: () =>
      client.messages.list({
        conversation_id: requireId(selectedChatConversation?.id),
        limit: 200,
      }),
    enabled: selectedChatConversation !== null,
    retry: false,
  });
  const messages = (messagesQuery.data?.items ?? []).filter(
    (message) => developerMode || message.visibility === "user",
  );

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
  const sandboxHealthy = runtimeHealthQuery.data?.executor.available === true;
  const capabilitiesQuery = useQuery({
    queryKey: [...workspaceKey, "capabilities", permissionProfile, sandboxHealthy],
    queryFn: () =>
      client.capabilities.get({
        profile: permissionProfile,
        sandbox_healthy: sandboxHealthy,
        overrides: {},
      }),
    enabled: healthQuery.isSuccess,
    retry: false,
  });

  const invalidateWorkspace = useCallback(async () => {
    await queryClient.invalidateQueries({
      queryKey: workspaceKey,
      predicate: (query) => query.queryKey[1] !== "health",
    });
  }, [queryClient]);

  const chatAssistant = useAssistantTurn({
    client,
    conversationId: selectedChatConversation?.id ?? null,
    profileId: selectedProfileId,
    operationMode: "answer",
    events: allEvents,
    onSettled: invalidateWorkspace,
  });
  const projectAssistant = useAssistantTurn({
    client,
    conversationId: selectedConversation?.id ?? null,
    profileId: selectedProfileId,
    operationMode: "continue_current_chat_draft",
    events: allEvents,
    onTaskCreated: setTaskSelection,
    onSettled: invalidateWorkspace,
  });

  useEffect(() => {
    if (!healthQuery.isSuccess) return;
    const controller = new AbortController();
    void (async () => {
      try {
        for await (const event of client.events.subscribe(eventCursor.current, {
          signal: controller.signal,
        })) {
          if (event.cursor <= eventCursor.current) continue;
          eventCursor.current = event.cursor;
          writeEventCursor(event.cursor);
          if (event.visibility !== "internal") {
            setAllEvents((current) => appendEvent(current, event));
          }
          if (terminalAssistantEvents.has(event.event_type)) {
            void queryClient.invalidateQueries({ queryKey: [...workspaceKey, "messages"] });
            void queryClient.invalidateQueries({ queryKey: [...workspaceKey, "tasks"] });
          } else if (event.event_type !== "assistant.message.delta") {
            void queryClient.invalidateQueries({
              queryKey: workspaceKey,
              predicate: (query) =>
                !["health", "messages", "providers", "provider-health"].includes(
                  String(query.queryKey[1]),
                ),
            });
          }
          setActionError(null);
        }
      } catch (error) {
        if (!controller.signal.aborted) setActionError(errorMessage(error));
      }
    })();
    return () => controller.abort();
  }, [client, healthQuery.isSuccess, queryClient]);

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
      selectProject(projectId: string) {
        setProjectSelection(projectId);
        setConversationSelection(null);
        setTaskSelection(null);
      },
      selectConversation(conversationId: string) {
        setConversationSelection(conversationId);
        setTaskSelection(null);
      },
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
      async createChatConversation() {
        const conversation = await runAction(() =>
          client.conversations.create({
            project_id: null,
            workspace_type: "chat_scratch",
          }),
        );
        setChatConversationSelection(conversation.id);
        chatAssistant.reset();
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
      chatAssistant,
      client,
      previewQuery.data?.preview,
      runAction,
      selectedProject,
      selectedTask,
      setChatConversationSelection,
      setConversationSelection,
      setProjectSelection,
      setTaskSelection,
    ],
  );

  const queryError = firstError(
    projectsQuery.error,
    conversationsQuery.error,
    providersQuery.error,
    providerHealthQuery.error,
    tasksQuery.error,
    messagesQuery.error,
    versionsQuery.error,
    approvalsQuery.error,
    previewQuery.error,
    runtimeHealthQuery.error,
    capabilitiesQuery.error,
  );
  const isLoading =
    healthQuery.isPending ||
    (healthQuery.isSuccess &&
      (projectsQuery.isPending ||
        conversationsQuery.isPending ||
        providersQuery.isPending ||
        providerHealthQuery.isPending)) ||
    (selectedProject !== null && conversationsQuery.isPending) ||
    (selectedConversation !== null && tasksQuery.isPending) ||
    (selectedChatConversation !== null && messagesQuery.isPending);
  const state = healthQuery.isError
    ? "offline"
    : isLoading
      ? "loading"
      : projects.length === 0
        ? "empty"
        : "ready";

  return {
    state,
    mode,
    statusLabel:
      state === "offline" ? "Core offline" : state === "loading" ? "Core starting" : "Core ready",
    errorMessage: queryError === null ? null : errorMessage(queryError),
    actionError,
    isActing,
    permissionProfile,
    developerMode,
    projects,
    conversations,
    chatConversations,
    tasks,
    versions,
    approvals,
    events: allEvents.filter(
      (event) => event.task_id === selectedTask?.id && event.visibility === "user",
    ),
    messages,
    providers,
    providerHealth,
    selectedProfileId,
    selectedProject,
    selectedConversation,
    selectedChatConversation,
    selectedTask,
    selectedVersion,
    preview: previewQuery.data ?? null,
    runtimeHealth: runtimeHealthQuery.data ?? null,
    capabilities: capabilitiesQuery.data ?? null,
    chatTurn: chatAssistant.turn,
    chatStreamedText: chatAssistant.streamedText,
    chatBusy: chatAssistant.isBusy,
    chatError: chatAssistant.error,
    projectTurn: projectAssistant.turn,
    projectBusy: projectAssistant.isBusy,
    projectError: projectAssistant.error,
    setMode,
    setPermissionProfile,
    setDeveloperMode,
    selectProfile: setProfileSelection,
    selectProject: actions.selectProject,
    selectConversation: actions.selectConversation,
    selectChatConversation: setChatConversationSelection,
    selectTask: setTaskSelection,
    createProject: actions.createProject,
    importProject: actions.importProject,
    createChatConversation: actions.createChatConversation,
    createTask: (userRequest) => projectAssistant.send(userRequest, []),
    sendChatMessage: chatAssistant.send,
    sendProjectMessage: projectAssistant.send,
    cancelChatTurn: chatAssistant.cancel,
    retryChatTurn: chatAssistant.retry,
    cancelProjectTurn: projectAssistant.cancel,
    decideApproval: actions.decideApproval,
    startPreview: actions.startPreview,
    stopPreview: actions.stopPreview,
    acceptVersion: actions.acceptVersion,
    discardVersion: actions.discardVersion,
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
  if (
    events.some(
      (event) => event.id === incoming.id || event.cursor === incoming.cursor,
    )
  ) {
    return events;
  }
  return [...events, incoming]
    .sort((left, right) => left.cursor - right.cursor)
    .slice(-500);
}

function firstError(...errors: (Error | null)[]): Error | null {
  return errors.find((error) => error !== null) ?? null;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Workspace request failed";
}

function readEventCursor(): number {
  try {
    const cursor = Number.parseInt(window.localStorage.getItem("fairy.events.cursor") ?? "0", 10);
    return Number.isSafeInteger(cursor) && cursor >= 0 ? cursor : 0;
  } catch {
    return 0;
  }
}

function writeEventCursor(cursor: number): void {
  try {
    window.localStorage.setItem("fairy.events.cursor", String(cursor));
  } catch {
    // Event delivery remains correct in memory when persistence is unavailable.
  }
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

function usePersistedEnum<T extends string>(
  key: string,
  fallback: T,
  allowed: readonly T[],
): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const stored = window.localStorage.getItem(key) as T | null;
      return stored !== null && allowed.includes(stored) ? stored : fallback;
    } catch {
      return fallback;
    }
  });
  const update = useCallback(
    (next: T) => {
      setValue(next);
      try {
        window.localStorage.setItem(key, next);
      } catch {
        // Preference persistence is optional.
      }
    },
    [key],
  );
  return [value, update];
}

function usePersistedBoolean(
  key: string,
  fallback: boolean,
): [boolean, (value: boolean) => void] {
  const [value, setValue] = useState(() => {
    try {
      const stored = window.localStorage.getItem(key);
      return stored === null ? fallback : stored === "true";
    } catch {
      return fallback;
    }
  });
  const update = useCallback(
    (next: boolean) => {
      setValue(next);
      try {
        window.localStorage.setItem(key, String(next));
      } catch {
        // Preference persistence is optional.
      }
    },
    [key],
  );
  return [value, update];
}
