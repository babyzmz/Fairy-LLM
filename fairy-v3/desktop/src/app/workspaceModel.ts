import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAssistantTurn } from "../chat/useAssistantTurn";
import { useTurnTraces } from "../chat/useTurnTraces";
import type { Conversation, EventEnvelope, McpToolPolicyInput, Project, Task } from "../core/client";
import { runEventDelivery } from "../core/eventStream";
import { CoreRpcError } from "../core/tauriTransport";
import type { McpServerDraft } from "../settings/extensionTypes";
import {
  selectedProfileId as profileIdForSelection,
  selectionBlockReason,
  selectionSupportsVision,
} from "../models/modelSelection";
import { useModelSelection } from "../models/useModelSelection";
import { useTaskMediaJobs } from "../media/useTaskMediaJobs";
import type { PermissionProfile, WorkspaceClient, WorkspaceMode, WorkspaceModel } from "./workspaceTypes";
export type { PermissionProfile, WorkspaceClient, WorkspaceMode, WorkspaceModel } from "./workspaceTypes";
import {
  readEventCheckpoint,
  usePersistedBoolean,
  usePersistedEnum,
  usePersistedSelection,
  writeEventCheckpoint,
} from "./workspacePreferences";
import { equalOverrides, extensionUpdateKey, permissionUpdateKey, requireMcpServer } from "./workspaceCommandKeys";
import { createWorkspaceFileActions } from "./workspaceFileActions";

const workspaceKey = ["workspace"] as const;
const permissionQueryKey = [...workspaceKey, "permissions"] as const;
const capabilityQueryKey = [...workspaceKey, "capabilities"] as const;
const coreStartupRetryLimit = 20;
const terminalAssistantEvents = new Set([
  "assistant.turn.completed",
  "assistant.turn.cancelled",
  "assistant.turn.failed",
]);

export function useWorkspaceModel(client: WorkspaceClient): WorkspaceModel {
  const queryClient = useQueryClient();
  const [mode, setMode] = usePersistedEnum<WorkspaceMode>("fairy.workspace.mode", "project", ["project", "chat"]);
  const [developerMode, setDeveloperMode] = usePersistedBoolean("fairy.workspace.developer", false);
  const [projectSelection, setProjectSelection] = usePersistedSelection("fairy.workspace.project");
  const [conversationSelection, setConversationSelection] = usePersistedSelection("fairy.workspace.conversation");
  const [chatConversationSelection, setChatConversationSelection] = usePersistedSelection(
    "fairy.workspace.chat-conversation",
  );
  const [taskSelection, setTaskSelection] = usePersistedSelection("fairy.workspace.task");
  const [allEvents, setAllEvents] = useState<EventEnvelope[]>([]);
  const eventCheckpoint = useRef(readEventCheckpoint());
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionErrorCode, setActionErrorCode] = useState<string | null>(null);
  const [isActing, setIsActing] = useState(false);
  const [chatTaskId, setChatTaskId] = useState<string | null>(null);
  const [petTaskId, setPetTaskId] = useState<string | null>(null);
  const petSubmissionRef = useRef(false);
  const petConversationIdRef = useRef<string | null>(null);

  const healthQuery = useQuery({
    queryKey: [...workspaceKey, "health"],
    queryFn: () => client.health(),
    retry: (failureCount, error) =>
      failureCount < coreStartupRetryLimit &&
      error instanceof CoreRpcError &&
      error.errorCode === "WORKER_INTERRUPTED",
    retryDelay: (attemptIndex) => Math.min(250 * (attemptIndex + 1), 1_000),
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
  const modelController = useModelSelection(client, healthQuery.isSuccess);

  const projects = projectsQuery.data?.items ?? [];
  const selectedProject = selectedItem(projects, projectSelection);
  const allConversations = conversationsQuery.data?.items ?? [];
  const conversations = allConversations.filter((conversation) => conversation.project_id === selectedProject?.id);
  const projectConversations = allConversations.filter(
    (conversation) => conversation.workspace_type === "project_chat",
  );
  const chatConversations = allConversations.filter(
    (conversation) => conversation.project_id === null && conversation.workspace_type === "chat_scratch",
  );
  const selectedConversation = selectedItem(conversations, conversationSelection);
  const selectedChatConversation = selectedItem(chatConversations, chatConversationSelection);
  const providers = providersQuery.data?.items ?? [];
  const providerHealth = providerHealthQuery.data?.items ?? [];
  const selectedProfileCandidate = profileIdForSelection(modelController.selection);
  const selectedProfileId = providers.some((profile) => profile.id === selectedProfileCandidate)
    ? selectedProfileCandidate
    : null;
  const modelBlockedReason = selectionBlockReason({
    catalog: modelController.catalog,
    selection: modelController.selection,
    providers,
    health: providerHealth,
  });
  const visionAvailable = selectionSupportsVision(
    modelController.catalog,
    modelController.selection,
  );

  const tasksQuery = useQuery({
    queryKey: [...workspaceKey, "tasks"],
    queryFn: () => client.tasks.list({ limit: 100 }),
    enabled: healthQuery.isSuccess,
    retry: false,
  });
  const allTasks = tasksQuery.data?.items ?? [];
  const tasks = allTasks.filter((task) => task.conversation_id === selectedConversation?.id);
  const selectedTask = selectedItem(tasks, taskSelection);
  const chatTasks = allTasks.filter((task) => task.conversation_id === selectedChatConversation?.id);
  const workspaceTask =
    mode === "chat" ? (chatTasks.find((task) => task.id === chatTaskId) ?? chatTasks.at(-1) ?? null) : selectedTask;
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
  const messageItems = messagesQuery.data?.items ?? [];
  const messages = messageItems.filter(
    (message) => developerMode || message.visibility === "user",
  );
  const projectMessagesQuery = useQuery({
    queryKey: [...workspaceKey, "project-messages", selectedConversation?.id],
    queryFn: () =>
      client.messages.list({
        conversation_id: requireId(selectedConversation?.id),
        limit: 100,
      }),
    enabled: mode === "project" && selectedConversation !== null,
    retry: false,
  });
  const projectMessageItems = projectMessagesQuery.data?.items ?? [];

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
  const selectedWorkspaceQuery = useQuery({
    queryKey: [
      ...workspaceKey,
      "workspace",
      workspaceTask?.conversation_id,
      workspaceTask?.workspace_id,
    ],
    queryFn: () => client.workspaces.get(requireId(workspaceTask?.workspace_id)),
    enabled: workspaceTask !== null,
    retry: false,
  });
  const previewQuery = useQuery({
    queryKey: [
      ...workspaceKey,
      "preview",
      workspaceTask?.conversation_id,
      workspaceTask?.id,
      workspaceTask?.target_version_id,
    ],
    queryFn: () =>
      client.previews.resolve({
        task_id: requireId(workspaceTask?.id),
        workspace_id: requireId(workspaceTask?.workspace_id),
        version_id: requireId(workspaceTask?.target_version_id),
      }),
    enabled: workspaceTask !== null && workspaceTask.target_version_id !== null && selectedWorkspaceQuery.isSuccess,
    retry: false,
  });
  const runtimeHealthQuery = useQuery({
    queryKey: [
      ...workspaceKey,
      "runtime-health",
      workspaceTask?.conversation_id,
      workspaceTask?.id,
    ],
    queryFn: () => client.runtimes.health(requireId(workspaceTask?.id)),
    enabled: workspaceTask !== null,
    retry: false,
  });
  const workspaceFilesQuery = useQuery({
    queryKey: [
      ...workspaceKey,
      "files",
      workspaceTask?.conversation_id,
      workspaceTask?.workspace_id,
      workspaceTask?.target_version_id,
    ],
    queryFn: () =>
      client.workspaces.listFiles({
        workspace_id: requireId(workspaceTask?.workspace_id),
        version_id: requireId(workspaceTask?.target_version_id),
      }),
    enabled: workspaceTask?.target_version_id !== null && workspaceTask !== null,
    retry: false,
  });
  const assetSetsQuery = useQuery({
    queryKey: [
      ...workspaceKey,
      "asset-sets",
      workspaceTask?.conversation_id,
      workspaceTask?.workspace_id,
      workspaceTask?.target_version_id,
    ],
    queryFn: () =>
      client.assetSets.list({
        workspace_id: requireId(workspaceTask?.workspace_id),
        version_id: requireId(workspaceTask?.target_version_id),
      }),
    enabled: workspaceTask?.target_version_id !== null && workspaceTask !== null,
    retry: false,
  });
  const mediaJobsQuery = useTaskMediaJobs(
    client,
    workspaceTask === null
      ? null
      : { conversationId: workspaceTask.conversation_id, taskId: workspaceTask.id },
  );
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
    modelSelection: modelController.selection,
    operationMode: "answer",
    events: allEvents,
    onTaskCreated(taskId) {
      setChatTaskId(taskId);
      if (petSubmissionRef.current) setPetTaskId(taskId);
    },
    onSettled: invalidateWorkspace,
  });
  const projectAssistant = useAssistantTurn({
    client,
    conversationId: selectedConversation?.id ?? null,
    profileId: selectedProfileId,
    modelSelection: modelController.selection,
    operationMode: "continue_current_chat_draft",
    events: allEvents,
    onTaskCreated: setTaskSelection,
    onSettled: invalidateWorkspace,
  });
  const { turnTraces, turnTraceStates, projectTrace, projectTraceState } = useTurnTraces(client, {
    enabled: healthQuery.isSuccess,
    chatMessages: messageItems,
    projectMessages: projectMessageItems,
    chatTurn: chatAssistant.turn,
    projectTurn: mode === "project" ? projectAssistant.turn : null,
    selectedTaskId: mode === "project" ? (selectedTask?.id ?? null) : null,
  });

  useEffect(() => {
    if (!healthQuery.isSuccess) return;
    const controller = new AbortController();
    void (async () => {
      try {
        await runEventDelivery(
          {
            sourceId: client.events.sourceId(),
            state: client.events.state,
            list: client.events.list,
            subscribe: client.events.subscribe,
          },
          {
            checkpoint: eventCheckpoint.current,
            signal: controller.signal,
            onCheckpoint(checkpoint) {
              eventCheckpoint.current = checkpoint;
              writeEventCheckpoint(checkpoint);
            },
            onEvent(event) {
              if (event.visibility !== "internal") {
                setAllEvents((current) => appendEvent(current, event));
              }
              if (terminalAssistantEvents.has(event.event_type) || event.event_type === "message.created") {
                void queryClient.invalidateQueries({
                  queryKey: workspaceKey,
                  predicate: (query) => ["messages", "project-messages"].includes(String(query.queryKey[1])),
                });
                void queryClient.invalidateQueries({ queryKey: [...workspaceKey, "tasks"] });
              } else if (event.event_type !== "assistant.message.delta") {
                void queryClient.invalidateQueries({
                  queryKey: workspaceKey,
                  predicate: (query) =>
                    !["health", "messages", "project-messages", "providers", "provider-health"].includes(
                      String(query.queryKey[1]),
                    ),
                });
              }
            },
          },
        );
      } catch (error) {
        if (!controller.signal.aborted) {
          setActionError(errorMessage(error));
          setActionErrorCode(coreErrorCode(error));
        }
      }
    })();
    return () => controller.abort();
  }, [client, healthQuery.isSuccess, queryClient]);

  const runAction = useCallback(
    async <T>(operation: () => Promise<T>): Promise<T> => {
      setIsActing(true);
      setActionError(null);
      setActionErrorCode(null);
      try {
        const result = await operation();
        await invalidateWorkspace();
        return result;
      } catch (error) {
        setActionError(errorMessage(error));
        setActionErrorCode(coreErrorCode(error));
        throw error;
      } finally {
        setIsActing(false);
      }
    },
    [invalidateWorkspace],
  );

  const persistPermissions = useCallback(
    async (profile: PermissionProfile, capabilityOverrides: Record<string, boolean>): Promise<void> => {
      const current = permissionsQuery.data;
      if (current === undefined) throw new Error("Core permission settings are unavailable");
      if (current.profile === profile && equalOverrides(current.capability_overrides, capabilityOverrides)) {
        return;
      }
      try {
        const updated = await runAction(() =>
          client.permissions.update({
            profile,
            capability_overrides: capabilityOverrides,
            expected_revision: current.revision,
            idempotency_key: permissionUpdateKey(current.revision, profile, capabilityOverrides),
          }),
        );
        queryClient.setQueryData(permissionQueryKey, updated);
      } catch (error) {
        if (coreErrorCode(error) !== "VERSION_CONFLICT") throw error;
        await Promise.allSettled([
          queryClient.refetchQueries({ queryKey: permissionQueryKey, exact: true }),
          queryClient.invalidateQueries({ queryKey: capabilityQueryKey }),
        ]);
        const conflict = new Error("Permissions changed on another device. Latest settings loaded; review and retry.");
        setActionError(conflict.message);
        setActionErrorCode("PERMISSION_CONFLICT");
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
    async (apiKey: string): Promise<void> => {
      const status = await runAction(() =>
        client.providers.configureOpenRouter({ api_key: apiKey }),
      );
      queryClient.setQueryData([...workspaceKey, "openrouter-status"], status);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: [...workspaceKey, "providers"] }),
        queryClient.invalidateQueries({ queryKey: [...workspaceKey, "provider-health"] }),
        modelController.refresh(),
      ]);
    },
    [client.providers, modelController, queryClient, runAction],
  );

  const selectProjectFolder = useCallback(
    () => runAction(() => client.projects.selectFolder()),
    [client.projects, runAction],
  );

  const deleteOpenRouter = useCallback(async (): Promise<void> => {
    const status = await runAction(() => client.providers.deleteOpenRouter());
    queryClient.setQueryData([...workspaceKey, "openrouter-status"], status);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: [...workspaceKey, "providers"] }),
      queryClient.invalidateQueries({ queryKey: [...workspaceKey, "provider-health"] }),
      modelController.refresh(),
    ]);
  }, [client.providers, modelController, queryClient, runAction]);

  const configureMcpServer = useCallback(
    async (input: McpServerDraft): Promise<void> => {
      const current = mcpServersQuery.data?.items.find((server) => server.server_id === input.serverId);
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
          idempotency_key: extensionUpdateKey("configure", input.serverId, expectedRevision, input),
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
          idempotency_key: extensionUpdateKey("discover", serverId, current.revision, { taskId }),
        }),
      );
    },
    [chatTaskId, client.mcp.servers, mcpServersQuery.data?.items, runAction, selectedTask?.id],
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
          idempotency_key: extensionUpdateKey("accept", serverId, current.revision, tools),
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
          idempotency_key: extensionUpdateKey(enabled ? "enable" : "disable", serverId, current.revision, { enabled }),
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
          idempotency_key: extensionUpdateKey("delete", serverId, current.revision, {}),
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
      selectChatConversation(conversationId: string) {
        setMode("chat");
        if (chatConversationSelection === conversationId) return;
        setChatConversationSelection(conversationId);
        setChatTaskId(null);
        setPetTaskId(null);
        petConversationIdRef.current = null;
        chatAssistant.reset();
      },
      async createProject(name: string) {
        const result = await runAction(() => client.projects.create({ name, residency: "local_only" }));
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
        setPetTaskId(null);
        petConversationIdRef.current = null;
        setMode("chat");
        chatAssistant.reset();
      },
      async createPetChatConversation() {
        const conversation = await runAction(() =>
          client.conversations.create({
            project_id: null,
            workspace_type: "chat_scratch",
          }),
        );
        petConversationIdRef.current = conversation.id;
        setChatConversationSelection(conversation.id);
        setChatTaskId(null);
        setPetTaskId(null);
        setMode("chat");
        chatAssistant.reset();
      },
      async renameConversation(conversation: Conversation, title: string) {
        await runAction(() =>
          client.conversations.update({
            conversation_id: conversation.id,
            title,
            expected_revision: conversation.revision,
          }),
        );
      },
      async setConversationPinned(conversation: Conversation, pinned: boolean) {
        await runAction(() =>
          client.conversations.update({
            conversation_id: conversation.id,
            pinned,
            expected_revision: conversation.revision,
          }),
        );
      },
      async deleteConversation(conversation: Conversation) {
        await runAction(() =>
          client.conversations.delete({
            conversation_id: conversation.id,
            expected_revision: conversation.revision,
            user_confirmed: true,
          }),
        );
        if (chatConversationSelection === conversation.id) {
          setChatConversationSelection(null);
          setChatTaskId(null);
          chatAssistant.reset();
        }
      },
      async moveConversationToProject(conversation: Conversation, project: Project) {
        const result = await runAction(() =>
          client.conversations.moveToProject({
            conversation_id: conversation.id,
            target_project_id: project.id,
            expected_revision: conversation.revision,
            user_confirmed: true,
            idempotency_key: `desktop:conversation-move:${conversation.id}:${project.id}`,
          }),
        );
        setProjectSelection(project.id);
        setConversationSelection(result.destination_conversation.id);
        setTaskSelection(null);
        setMode("project");
      },
      async sendPetMessage(value: string) {
        const text = value.trim();
        if (text.length === 0) return;
        let conversationId = petConversationIdRef.current ?? selectedChatConversation?.id ?? null;
        if (conversationId === null) {
          const conversation = await runAction(() =>
            client.conversations.create({
              project_id: null,
              workspace_type: "chat_scratch",
            }),
          );
          conversationId = conversation.id;
          setChatConversationSelection(conversation.id);
          setChatTaskId(null);
          chatAssistant.reset();
        }
        setMode("chat");
        petSubmissionRef.current = true;
        try {
          await chatAssistant.sendToConversation(conversationId, text, []);
        } finally {
          petSubmissionRef.current = false;
          petConversationIdRef.current = null;
        }
      },
      async renameTask(task: Task, title: string) {
        await runAction(() =>
          client.tasks.updateMetadata({
            task_id: task.id,
            display_title: title,
            expected_revision: task.metadata_revision,
          }),
        );
      },
      async setTaskPinned(task: Task, pinned: boolean) {
        await runAction(() =>
          client.tasks.updateMetadata({
            task_id: task.id,
            pinned,
            expected_revision: task.metadata_revision,
          }),
        );
      },
      async archiveTask(task: Task) {
        await runAction(() =>
          client.tasks.archive({
            task_id: task.id,
            expected_revision: task.metadata_revision,
          }),
        );
      },
      async decideApproval(approvalId: string, approved: boolean) {
        const result = await runAction(() =>
          client.approvals.decide({
            approval_id: approvalId,
            approved,
          }),
        );
        if (result.resume_requested && result.assistant_turn_id !== null) {
          chatAssistant.markApprovalResume(result.assistant_turn_id);
          projectAssistant.markApprovalResume(result.assistant_turn_id);
        }
      },
      async startPreview() {
        if (workspaceTask === null) throw new Error("Task is unavailable");
        const workspace = selectedWorkspaceQuery.data;
        const versionId = workspaceTask.target_version_id;
        if (workspace === undefined || versionId === null) {
          throw new Error("Workspace Version is unavailable");
        }
        await runAction(() =>
          client.previews.start({
            task_id: workspaceTask.id,
            workspace_id: workspaceTask.workspace_id,
            version_id: versionId,
            expected_workspace_revision: workspace.revision,
            idempotency_key: `desktop:preview:${workspaceTask.id}`,
          }),
        );
      },
      async stopPreview() {
        const activePreview = previewQuery.data?.preview;
        const workspace = selectedWorkspaceQuery.data;
        if (activePreview === undefined || activePreview === null) return;
        if (workspaceTask === null || workspace === undefined) return;
        await runAction(() =>
          client.previews.stop({
            preview_id: activePreview.id,
            task_id: workspaceTask.id,
            workspace_id: workspaceTask.workspace_id,
            version_id: requireId(workspaceTask.target_version_id),
            expected_workspace_revision: workspace.revision,
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
        const workspace = selectedWorkspaceQuery.data;
        await runAction(async () => {
          if (
            activePreview !== undefined &&
            activePreview !== null &&
            !["stopped", "failed"].includes(activePreview.status)
          ) {
            if (workspace === undefined) throw new Error("Workspace is unavailable");
            await client.previews.stop({
              preview_id: activePreview.id,
              task_id: selectedTask.id,
              workspace_id: selectedTask.workspace_id,
              version_id: requireId(selectedTask.target_version_id),
              expected_workspace_revision: workspace.revision,
              idempotency_key: `desktop:preview:${activePreview.id}:discard-stop`,
            });
          }
          await client.versions.discard(selectedTask.id);
        });
      },
      async listDocuments() {
        const taskId = requireId(selectedTask?.id ?? chatTaskId);
        const page = await client.documents.list({ task_id: taskId, limit: 100 });
        return page.items;
      },
      async searchDocuments(query: string) {
        const taskId = requireId(selectedTask?.id ?? chatTaskId);
        const page = await client.documents.search({ task_id: taskId, query, limit: 20 });
        return page.items;
      },
      async deleteDocument(documentId: string) {
        const taskId = requireId(selectedTask?.id ?? chatTaskId);
        await runAction(() =>
          client.documents.delete({
            task_id: taskId,
            document_id: documentId,
            user_confirmed: true,
            idempotency_key: `desktop:document-delete:${documentId}:${crypto.randomUUID()}`,
          }),
        );
      },
      async searchMemory(query: string) {
        const taskId = requireId(selectedTask?.id ?? chatTaskId);
        const page = await client.memory.search({ task_id: taskId, query, limit: 20 });
        return page.items;
      },
      async forgetMemory(targetKind: "observation" | "claim", targetId: string) {
        const taskId = requireId(selectedTask?.id ?? chatTaskId);
        await runAction(() =>
          client.memory.forget({
            task_id: taskId,
            target_kind: targetKind,
            target_id: targetId,
            reason: "User requested removal from the desktop Knowledge panel.",
            user_confirmed: true,
            idempotency_key: `desktop:memory-forget:${targetId}:${crypto.randomUUID()}`,
          }),
        );
      },
      async copyMessage(taskId: string, content: string) {
        await runAction(() =>
          client.systemActions.execute({
            task_id: taskId,
            action: { type: "copy_text", text: content },
            idempotency_key: `desktop:message-copy:${crypto.randomUUID()}`,
            user_confirmed: true,
          }),
        );
      },
      async openMessageLink(taskId: string, url: string) {
        await runAction(() =>
          client.systemActions.execute({
            task_id: taskId,
            action: { type: "open_url", url },
            idempotency_key: `desktop:message-link:${crypto.randomUUID()}`,
            user_confirmed: true,
          }),
        );
      },
      ...createWorkspaceFileActions({
        client,
        mode,
        task: workspaceTask,
        conversation: mode === "chat" ? selectedChatConversation : selectedConversation,
        workspace: selectedWorkspaceQuery.data,
        runAction,
        selectTask: mode === "chat" ? setChatTaskId : setTaskSelection,
        invalidateWorkspace,
        refreshFiles: () =>
          queryClient.invalidateQueries({
            queryKey: [...workspaceKey, "files"],
          }),
      }),
    }),
    [
      chatAssistant,
      chatConversationSelection,
      chatTaskId,
      client,
      previewQuery.data?.preview,
      projectAssistant,
      invalidateWorkspace,
      mode,
      runAction,
      selectedProject,
      selectedChatConversation,
      selectedConversation,
      selectedWorkspaceQuery.data,
      selectedTask,
      workspaceTask,
      queryClient,
      setChatConversationSelection,
      setConversationSelection,
      setMode,
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
    modelController.error,
    skillsQuery.error,
    mcpServersQuery.error,
    tasksQuery.error,
    messagesQuery.error,
    projectMessagesQuery.error,
    versionsQuery.error,
    approvalsQuery.error,
    chatApprovalsQuery.error,
    previewQuery.error,
    runtimeHealthQuery.error,
    workspaceFilesQuery.error,
    assetSetsQuery.error,
    mediaJobsQuery.error,
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
    modelController.loading ||
    (healthQuery.isSuccess && (skillsQuery.isPending || mcpServersQuery.isPending)) ||
    (selectedProject !== null && conversationsQuery.isPending) ||
    (selectedConversation !== null && tasksQuery.isPending) ||
    (selectedChatConversation !== null && messagesQuery.isPending);
  const state = healthQuery.isError ? "offline" : isLoading ? "loading" : projects.length === 0 ? "empty" : "ready";

  return {
    state,
    mode,
    statusLabel: state === "offline" ? "Core offline" : state === "loading" ? "Core starting" : "Core ready",
    errorMessage: queryError === null ? null : errorMessage(queryError),
    actionError,
    actionErrorCode,
    isActing,
    permissionProfile,
    permissionSettings: permissionsQuery.data ?? null,
    developerMode,
    projects,
    conversations,
    projectConversations,
    chatConversations,
    tasks,
    allTasks,
    versions,
    approvals,
    chatApprovals,
    events: allEvents.filter(
      (event) =>
        event.task_id === selectedTask?.id &&
        (event.visibility === "user" || developerMode && event.visibility === "developer"),
    ),
    chatEvents: allEvents.filter(
      (event) =>
        event.conversation_id === selectedChatConversation?.id &&
        (event.visibility === "user" || developerMode && event.visibility === "developer"),
    ),
    presenceEvents: allEvents.filter((event) => event.visibility === "user"),
    messages,
    providers,
    providerHealth,
    openRouterStatus: openRouterStatusQuery.data ?? null,
    skills: skillsQuery.data?.items ?? [],
    mcpServers: mcpServersQuery.data?.items ?? [],
    selectedProfileId,
    modelCatalog: modelController.catalog,
    modelSelection: modelController.selection,
    modelSelectionLoading: modelController.loading,
    modelSelectionRefreshing: modelController.refreshing,
    modelSelectionBlockReason: modelBlockedReason,
    visionAvailable,
    selectedProject,
    selectedConversation,
    selectedChatConversation,
    selectedTask,
    workspaceTask,
    selectedVersion,
    preview: previewQuery.data ?? null,
    runtimeHealth: runtimeHealthQuery.data ?? null,
    workspaceFiles: workspaceFilesQuery.data?.items ?? [],
    workspaceGeneration: workspaceFilesQuery.data?.generation ?? 0,
    mediaJobs: mediaJobsQuery.data?.items ?? [],
    assetSets: assetSetsQuery.data?.items ?? [],
    workspaceFilesLoading: workspaceFilesQuery.isPending && workspaceFilesQuery.isEnabled,
    mediaJobsLoading: mediaJobsQuery.isPending && mediaJobsQuery.isEnabled,
    capabilities: capabilitiesQuery.data ?? null,
    chatTurn: chatAssistant.turn,
    turnTraces,
    turnTraceStates,
    chatStreamedText: chatAssistant.streamedText,
    chatPendingUserMessage: chatAssistant.pendingUserMessage,
    chatBusy: chatAssistant.isBusy,
    chatError: chatAssistant.error,
    projectTurn: projectAssistant.turn,
    projectTrace,
    projectTraceState,
    projectBusy: projectAssistant.isBusy,
    projectError: projectAssistant.error,
    petTaskId,
    setMode,
    setPermissionProfile,
    setCapabilityEnabled,
    configureMcpServer,
    discoverMcpServer,
    acceptMcpServer,
    setMcpServerEnabled,
    deleteMcpServer,
    listDocuments: actions.listDocuments,
    searchDocuments: actions.searchDocuments,
    deleteDocument: actions.deleteDocument,
    searchMemory: actions.searchMemory,
    forgetMemory: actions.forgetMemory,
    setDeveloperMode,
    selectModel: (selectionMode, modelId) =>
      runAction(() => modelController.update({ mode: selectionMode, model_id: modelId })),
    refreshModelCatalog: () => runAction(modelController.refresh),
    configureOpenRouter,
    deleteOpenRouter,
    selectProject: actions.selectProject,
    selectConversation: actions.selectConversation,
    selectChatConversation: actions.selectChatConversation,
    selectTask: setTaskSelection,
    createProject: actions.createProject,
    importProject: actions.importProject,
    selectProjectFolder,
    createChatConversation: actions.createChatConversation,
    createPetChatConversation: actions.createPetChatConversation,
    renameConversation: actions.renameConversation,
    setConversationPinned: actions.setConversationPinned,
    deleteConversation: actions.deleteConversation,
    moveConversationToProject: actions.moveConversationToProject,
    renameTask: actions.renameTask,
    setTaskPinned: actions.setTaskPinned,
    archiveTask: actions.archiveTask,
    createTask: (userRequest) => projectAssistant.send(userRequest, []),
    sendChatMessage: chatAssistant.send,
    sendProjectMessage: async (...args: Parameters<typeof projectAssistant.send>) => {
      if (permissionProfile === "observe") {
        const message = "Project Tasks require Standard or Autonomous permissions. Observe remains read-only.";
        setActionError(message);
        setActionErrorCode("CAPABILITY_NOT_AVAILABLE");
        throw new Error(message);
      }
      await projectAssistant.send(...args);
    },
    sendPetMessage: actions.sendPetMessage,
    cancelPetTurn: async () => {
      if (petTaskId === null || chatAssistant.turn?.task_id !== petTaskId) return;
      await chatAssistant.cancel();
    },
    cancelChatTurn: chatAssistant.cancel,
    retryChatTurn: chatAssistant.retry,
    retryPendingChatMessage: chatAssistant.retryPending,
    deletePendingChatMessage: chatAssistant.deletePending,
    takePendingChatMessageForEdit: chatAssistant.takePendingForEdit,
    copyMessage: actions.copyMessage,
    openMessageLink: actions.openMessageLink,
    readWorkspaceFile: actions.readWorkspaceFile,
    openWorkspaceFileStream: actions.openWorkspaceFileStream,
    presentWorkspaceFile: actions.presentWorkspaceFile,
    compareWorkspaceFile: actions.compareWorkspaceFile,
    resolveWorkspaceFileSet: actions.resolveWorkspaceFileSet,
    listFileAnnotations: actions.listFileAnnotations,
    updateFileAnnotations: actions.updateFileAnnotations,
    createTextSelection: actions.createTextSelection,
    createSceneSelection: actions.createSceneSelection,
    revealWorkspaceFile: actions.revealWorkspaceFile,
    refreshWorkspaceFiles: actions.refreshWorkspaceFiles,
    uploadWorkspaceFiles: actions.uploadWorkspaceFiles,
    renameWorkspaceFile: actions.renameWorkspaceFile,
    deleteWorkspaceFile: actions.deleteWorkspaceFile,
    exportWorkspace: actions.exportWorkspace,
    cancelMediaJob: (job) =>
      runAction(() =>
        client.media.videos.cancel({
          job_id: job.id,
          expected_revision: job.revision,
          idempotency_key: `desktop:media-cancel:${job.id}:${job.revision}`,
          user_confirmed: true,
        }),
      ).then(() => undefined),
    cancelProjectTurn: projectAssistant.cancel,
    decideApproval: actions.decideApproval,
    startPreview: actions.startPreview,
    stopPreview: actions.stopPreview,
    reviewTask: actions.reviewTask,
    acceptVersion: actions.acceptVersion,
    discardVersion: actions.discardVersion,
    retryWorkspace: async () => {
      setActionError(null);
      setActionErrorCode(null);
      await queryClient.resetQueries({ queryKey: workspaceKey });
    },
    openSettings: () => client.desktop.openSettings(),
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
  if (events.some((event) => event.id === incoming.id || event.cursor === incoming.cursor)) {
    return events;
  }
  return [...events, incoming].sort((left, right) => left.cursor - right.cursor).slice(-500);
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
