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
  ExecutionSettings,
  Message,
  McpServer,
  McpToolPolicyInput,
  PreviewContext,
  Project,
  ProviderHealth,
  ProviderProfile,
  OpenRouterConfigurationStatus,
  RuntimeHealth,
  Skill,
  Task,
  Version,
} from "../core/client";
import type { PendingImageAttachment } from "../perception/CaptureControl";
import type { McpServerDraft } from "../settings/extensionTypes";

export type WorkspaceMode = "project" | "chat";
export type PermissionProfile = "observe" | "standard" | "autonomous";

export interface WorkspaceClient extends AssistantTurnClient {
  health: CoreClient["health"];
  projects: Pick<CoreClient["projects"], "list" | "create" | "import">;
  conversations: Pick<CoreClient["conversations"], "list" | "create">;
  tasks: Pick<CoreClient["tasks"], "list" | "create" | "review">;
  approvals: Pick<CoreClient["approvals"], "list" | "decide">;
  versions: Pick<CoreClient["versions"], "list" | "accept" | "discard">;
  runtimes: Pick<CoreClient["runtimes"], "health">;
  previews: Pick<CoreClient["previews"], "resolve" | "start" | "stop">;
  capabilities: Pick<CoreClient["capabilities"], "get">;
  permissions: Pick<CoreClient["permissions"], "get" | "update">;
  providers: Pick<
    CoreClient["providers"],
    "list" | "health" | "openRouterStatus" | "configureOpenRouter" | "deleteOpenRouter"
  >;
  skills: Pick<CoreClient["skills"], "list">;
  mcp: {
    servers: Pick<
      CoreClient["mcp"]["servers"],
      "list" | "configure" | "discover" | "accept" | "setEnabled" | "delete"
    >;
  };
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
  permissionProfile: PermissionProfile | null;
  permissionSettings: ExecutionSettings | null;
  developerMode: boolean;
  projects: Project[];
  conversations: Conversation[];
  chatConversations: Conversation[];
  tasks: Task[];
  versions: Version[];
  approvals: Approval[];
  chatApprovals: Approval[];
  events: EventEnvelope[];
  presenceEvents: EventEnvelope[];
  messages: Message[];
  providers: ProviderProfile[];
  providerHealth: ProviderHealth[];
  openRouterStatus: OpenRouterConfigurationStatus | null;
  skills: Skill[];
  mcpServers: McpServer[];
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
  setPermissionProfile(profile: PermissionProfile): Promise<void>;
  setCapabilityEnabled(name: string, enabled: boolean): Promise<void>;
  configureMcpServer(input: McpServerDraft): Promise<void>;
  discoverMcpServer(serverId: string): Promise<void>;
  acceptMcpServer(serverId: string, tools: McpToolPolicyInput[]): Promise<void>;
  setMcpServerEnabled(serverId: string, enabled: boolean): Promise<void>;
  deleteMcpServer(serverId: string): Promise<void>;
  setDeveloperMode(enabled: boolean): void;
  selectProfile(profileId: string): void;
  configureOpenRouter(apiKey: string, modelId: string): Promise<void>;
  deleteOpenRouter(): Promise<void>;
  selectProject(projectId: string): void;
  selectConversation(conversationId: string): void;
  selectChatConversation(conversationId: string): void;
  selectTask(taskId: string): void;
  createProject(name: string): Promise<void>;
  importProject(name: string, sourcePath: string): Promise<void>;
  createChatConversation(): Promise<void>;
  createTask(userRequest: string): Promise<void>;
  sendChatMessage(
    value: string,
    files: File[],
    images?: PendingImageAttachment[],
  ): Promise<void>;
  sendProjectMessage(
    value: string,
    files: File[],
    images?: PendingImageAttachment[],
  ): Promise<void>;
  cancelChatTurn(): Promise<void>;
  retryChatTurn(): Promise<void>;
  cancelProjectTurn(): Promise<void>;
  decideApproval(approvalId: string, approved: boolean): Promise<void>;
  startPreview(): Promise<void>;
  stopPreview(): Promise<void>;
  reviewTask(): Promise<void>;
  acceptVersion(): Promise<void>;
  discardVersion(): Promise<void>;
}

const workspaceKey = ["workspace"] as const;
const permissionQueryKey = [...workspaceKey, "permissions"] as const;
const capabilityQueryKey = [...workspaceKey, "capabilities"] as const;
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
  const [chatTaskId, setChatTaskId] = useState<string | null>(null);

  const healthQuery = useQuery({
    queryKey: [...workspaceKey, "health"],
    queryFn: () => client.health(),
    retry: false,
    refetchOnWindowFocus: false,
  });
  const permissionsQuery = useQuery({
    queryKey: permissionQueryKey,
    queryFn: () => client.permissions.get(),
    enabled: healthQuery.isSuccess,
    retry: false,
  });
  const permissionProfile = permissionsQuery.data?.profile ?? null;
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
  const skillsQuery = useQuery({
    queryKey: [...workspaceKey, "skills"],
    queryFn: () => client.skills.list(),
    enabled: healthQuery.isSuccess,
    retry: false,
  });
  const mcpServersQuery = useQuery({
    queryKey: [...workspaceKey, "mcp-servers"],
    queryFn: () => client.mcp.servers.list(),
    enabled: healthQuery.isSuccess,
    retry: false,
  });
  const providerHealthQuery = useQuery({
    queryKey: [...workspaceKey, "provider-health"],
    queryFn: () => client.providers.health(),
    enabled: healthQuery.isSuccess,
    retry: false,
  });
  const openRouterStatusQuery = useQuery({
    queryKey: [...workspaceKey, "openrouter-status"],
    queryFn: () => client.providers.openRouterStatus(),
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
        limit: 100,
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
  const chatApprovalsQuery = useQuery({
    queryKey: [...workspaceKey, "chat-approvals", chatTaskId],
    queryFn: () => client.approvals.list({ task_id: requireId(chatTaskId) }),
    enabled: chatTaskId !== null,
    retry: false,
  });
  const chatApprovals = chatApprovalsQuery.data?.items ?? [];
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
    queryKey: [...capabilityQueryKey, permissionsQuery.data?.revision],
    queryFn: () => client.capabilities.get(),
    enabled: permissionsQuery.isSuccess,
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
    onTaskCreated: setChatTaskId,
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

  const persistPermissions = useCallback(
    async (
      profile: PermissionProfile,
      capabilityOverrides: Record<string, boolean>,
    ): Promise<void> => {
      const current = permissionsQuery.data;
      if (current === undefined) throw new Error("Core permission settings are unavailable");
      if (
        current.profile === profile &&
        equalOverrides(current.capability_overrides, capabilityOverrides)
      ) {
        return;
      }
      try {
        const updated = await runAction(() =>
          client.permissions.update({
            profile,
            capability_overrides: capabilityOverrides,
            expected_revision: current.revision,
            idempotency_key: permissionUpdateKey(
              current.revision,
              profile,
              capabilityOverrides,
            ),
          }),
        );
        queryClient.setQueryData(permissionQueryKey, updated);
      } catch (error) {
        if (coreErrorCode(error) !== "VERSION_CONFLICT") throw error;
        await Promise.allSettled([
          queryClient.refetchQueries({ queryKey: permissionQueryKey, exact: true }),
          queryClient.invalidateQueries({ queryKey: capabilityQueryKey }),
        ]);
        const conflict = new Error(
          "Permissions changed on another device. Latest settings loaded; review and retry.",
        );
        setActionError(conflict.message);
        throw conflict;
      }
    },
    [client.permissions, permissionsQuery.data, queryClient, runAction],
  );

  const setPermissionProfile = useCallback(
    async (profile: PermissionProfile): Promise<void> => {
      const current = permissionsQuery.data;
      if (current === undefined) throw new Error("Core permission settings are unavailable");
      await persistPermissions(profile, current.capability_overrides);
    },
    [permissionsQuery.data, persistPermissions],
  );

  const setCapabilityEnabled = useCallback(
    async (name: string, enabled: boolean): Promise<void> => {
      const current = permissionsQuery.data;
      const known = capabilitiesQuery.data?.command_metadata.some(
        (definition) => definition.name === name && definition.model_visible,
      );
      if (current === undefined || !known) {
        throw new Error("Core capability metadata is unavailable");
      }
      const overrides = { ...current.capability_overrides };
      if (enabled) delete overrides[name];
      else overrides[name] = false;
      await persistPermissions(current.profile, overrides);
    },
    [capabilitiesQuery.data?.command_metadata, permissionsQuery.data, persistPermissions],
  );

  const configureOpenRouter = useCallback(
    async (apiKey: string, modelId: string): Promise<void> => {
      const status = await runAction(() =>
        client.providers.configureOpenRouter({ api_key: apiKey, model_id: modelId }),
      );
      queryClient.setQueryData([...workspaceKey, "openrouter-status"], status);
      setProfileSelection("openrouter");
    },
    [client.providers, queryClient, runAction, setProfileSelection],
  );

  const deleteOpenRouter = useCallback(async (): Promise<void> => {
    const status = await runAction(() => client.providers.deleteOpenRouter());
    queryClient.setQueryData([...workspaceKey, "openrouter-status"], status);
    setProfileSelection(null);
  }, [client.providers, queryClient, runAction, setProfileSelection]);

  const configureMcpServer = useCallback(
    async (input: McpServerDraft): Promise<void> => {
      const current = mcpServersQuery.data?.items.find(
        (server) => server.server_id === input.serverId,
      );
      const expectedRevision = current?.revision ?? 0;
      await runAction(() =>
        client.mcp.servers.configure({
          server_id: input.serverId,
          display_name: input.displayName,
          transport: input.transport,
          command: input.command,
          arguments: input.arguments,
          endpoint: input.endpoint,
          credential_ref: input.credentialRef,
          environment_refs: input.environmentRefs,
          expected_revision: expectedRevision,
          idempotency_key: extensionUpdateKey(
            "configure",
            input.serverId,
            expectedRevision,
            input,
          ),
        }),
      );
    },
    [client.mcp.servers, mcpServersQuery.data?.items, runAction],
  );

  const discoverMcpServer = useCallback(
    async (serverId: string): Promise<void> => {
      const current = requireMcpServer(mcpServersQuery.data?.items, serverId);
      const taskId = selectedTask?.id ?? chatTaskId;
      if (taskId === null) throw new Error("A durable Task is required to discover MCP tools");
      await runAction(() =>
        client.mcp.servers.discover({
          server_id: serverId,
          task_id: taskId,
          expected_revision: current.revision,
          idempotency_key: extensionUpdateKey(
            "discover",
            serverId,
            current.revision,
            { taskId },
          ),
        }),
      );
    }, [chatTaskId, client.mcp.servers, mcpServersQuery.data?.items, runAction, selectedTask?.id],
  );

  const acceptMcpServer = useCallback(
    async (serverId: string, tools: McpToolPolicyInput[]): Promise<void> => {
      const current = requireMcpServer(mcpServersQuery.data?.items, serverId);
      if (current.pending_schema_digest === null) {
        throw new Error("MCP server has no pending schema to accept");
      }
      await runAction(() =>
        client.mcp.servers.accept({
          server_id: serverId,
          expected_revision: current.revision,
          schema_digest: current.pending_schema_digest as string,
          enabled: true,
          tools,
          idempotency_key: extensionUpdateKey(
            "accept",
            serverId,
            current.revision,
            tools,
          ),
        }),
      );
    },
    [client.mcp.servers, mcpServersQuery.data?.items, runAction],
  );

  const setMcpServerEnabled = useCallback(
    async (serverId: string, enabled: boolean): Promise<void> => {
      const current = requireMcpServer(mcpServersQuery.data?.items, serverId);
      await runAction(() =>
        client.mcp.servers.setEnabled({
          server_id: serverId,
          expected_revision: current.revision,
          enabled,
          idempotency_key: extensionUpdateKey(
            enabled ? "enable" : "disable",
            serverId,
            current.revision,
            { enabled },
          ),
        }),
      );
    },
    [client.mcp.servers, mcpServersQuery.data?.items, runAction],
  );

  const deleteMcpServer = useCallback(
    async (serverId: string): Promise<void> => {
      const current = requireMcpServer(mcpServersQuery.data?.items, serverId);
      await runAction(() =>
        client.mcp.servers.delete({
          server_id: serverId,
          expected_revision: current.revision,
          idempotency_key: extensionUpdateKey(
            "delete",
            serverId,
            current.revision,
            {},
          ),
        }),
      );
    },
    [client.mcp.servers, mcpServersQuery.data?.items, runAction],
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
        setChatTaskId(null);
        chatAssistant.reset();
      },
      async decideApproval(approvalId: string, approved: boolean) {
        const result = await runAction(() =>
          client.approvals.decide({
            approval_id: approvalId,
            approved,
          }),
        );
        if (result.approval.tool_invocation_id === null) return;
        if (result.approval.task_id === chatAssistant.turn?.task_id) {
          await chatAssistant.resume();
        } else if (result.approval.task_id === projectAssistant.turn?.task_id) {
          await projectAssistant.resume();
        }
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
      async reviewTask() {
        if (selectedTask === null) throw new Error("Task is unavailable");
        await runAction(() => client.tasks.review(selectedTask.id));
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
      projectAssistant,
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
    openRouterStatusQuery.error,
    skillsQuery.error,
    mcpServersQuery.error,
    tasksQuery.error,
    messagesQuery.error,
    versionsQuery.error,
    approvalsQuery.error,
    chatApprovalsQuery.error,
    previewQuery.error,
    runtimeHealthQuery.error,
    permissionsQuery.error,
    capabilitiesQuery.error,
  );
  const isLoading =
    healthQuery.isPending ||
    (healthQuery.isSuccess && permissionsQuery.isPending) ||
    (healthQuery.isSuccess &&
      (projectsQuery.isPending ||
        conversationsQuery.isPending ||
        providersQuery.isPending ||
        providerHealthQuery.isPending ||
        openRouterStatusQuery.isPending)) ||
    (healthQuery.isSuccess && (skillsQuery.isPending || mcpServersQuery.isPending)) ||
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
    permissionSettings: permissionsQuery.data ?? null,
    developerMode,
    projects,
    conversations,
    chatConversations,
    tasks,
    versions,
    approvals,
    chatApprovals,
    events: allEvents.filter(
      (event) => event.task_id === selectedTask?.id && event.visibility === "user",
    ),
    presenceEvents: allEvents.filter((event) => event.visibility === "user"),
    messages,
    providers,
    providerHealth,
    openRouterStatus: openRouterStatusQuery.data ?? null,
    skills: skillsQuery.data?.items ?? [],
    mcpServers: mcpServersQuery.data?.items ?? [],
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
    setCapabilityEnabled,
    configureMcpServer,
    discoverMcpServer,
    acceptMcpServer,
    setMcpServerEnabled,
    deleteMcpServer,
    setDeveloperMode,
    selectProfile: setProfileSelection,
    configureOpenRouter,
    deleteOpenRouter,
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
    reviewTask: actions.reviewTask,
    acceptVersion: actions.acceptVersion,
    discardVersion: actions.discardVersion,
  };
}

function selectedItem<T extends { id: string }>(items: T[], selectedId: string | null): T | null {
  return items.find((item) => item.id === selectedId) ?? items.at(-1) ?? null;
}

function requireId(value: string | null | undefined): string {
  if (value === undefined || value === null) {
    throw new Error("Workspace scope is unavailable");
  }
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

function coreErrorCode(error: unknown): string | null {
  if (typeof error !== "object" || error === null || !("errorCode" in error)) {
    return null;
  }
  return typeof error.errorCode === "string" ? error.errorCode : null;
}

function equalOverrides(
  left: Record<string, boolean>,
  right: Record<string, boolean>,
): boolean {
  const leftEntries = Object.entries(left).sort(([a], [b]) => a.localeCompare(b));
  const rightEntries = Object.entries(right).sort(([a], [b]) => a.localeCompare(b));
  return JSON.stringify(leftEntries) === JSON.stringify(rightEntries);
}

function permissionUpdateKey(
  revision: number,
  profile: PermissionProfile,
  overrides: Record<string, boolean>,
): string {
  const canonical = JSON.stringify({
    profile,
    overrides: Object.entries(overrides).sort(([a], [b]) => a.localeCompare(b)),
  });
  let hash = 0xcbf29ce484222325n;
  for (const byte of new TextEncoder().encode(canonical)) {
    hash ^= BigInt(byte);
    hash = BigInt.asUintN(64, hash * 0x100000001b3n);
  }
  return `permissions:${revision}:${hash.toString(16).padStart(16, "0")}`;
}

function requireMcpServer(
  servers: McpServer[] | undefined,
  serverId: string,
): McpServer {
  const server = servers?.find((item) => item.server_id === serverId);
  if (server === undefined) throw new Error("MCP server is unavailable");
  return server;
}

function extensionUpdateKey(
  operation: string,
  serverId: string,
  revision: number,
  payload: unknown,
): string {
  const canonical = JSON.stringify(canonicalValue(payload));
  let hash = 0xcbf29ce484222325n;
  for (const byte of new TextEncoder().encode(canonical)) {
    hash ^= BigInt(byte);
    hash = BigInt.asUintN(64, hash * 0x100000001b3n);
  }
  return `mcp:${serverId}:${operation}:${revision}:${hash.toString(16).padStart(16, "0")}`;
}

function canonicalValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (typeof value !== "object" || value === null) return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => [key, canonicalValue(item)]),
  );
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
