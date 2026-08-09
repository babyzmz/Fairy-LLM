import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAssistantTurn } from "../chat/useAssistantTurn";
import { useTurnTraces } from "../chat/useTurnTraces";
import type { EventEnvelope, Message, Task } from "../core/client";
import { runResilientEventDelivery } from "../core/eventStream";
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
import { createWorkspaceFileActions } from "./workspaceFileActions";
import { previewStartIdempotencyKey } from "./workspacePreviewActions";
import { usePreviewActivation } from "./usePreviewActivation";
import { useAssistantScheduling } from "./useAssistantScheduling";
import { useWorkspacePermissions } from "./useWorkspacePermissions";
import { useWorkspaceKnowledge } from "./workspaceKnowledgeModel";
import { useWorkspaceBrowser } from "./workspaceBrowserModel";
import {
  collectCursorPages,
  createWorkspaceHistoryActions,
  projectOverviewSelection,
  sortHistoryItems,
} from "./workspaceHistoryActions";
import {
  appendEvent,
  capabilityQueryKey,
  coreErrorCode,
  errorMessage,
  firstError,
  requireId,
  selectedItem,
  selectWorkspaceTask,
  workspaceDisplayError,
  workspaceKey,
} from "./workspaceModelUtils";
import {
  messageCacheStaleTime,
  useConversationPrefetch,
  useWorkspaceBaseQueries,
  useWorkspaceInvalidation,
} from "./workspaceQueryModel";


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
  const [eventStreamError, setEventStreamError] = useState<string | null>(null);
  const [eventStreamErrorCode, setEventStreamErrorCode] = useState<string | null>(null);
  const [isActing, setIsActing] = useState(false);
  const [chatTaskId, setChatTaskId] = useState<string | null>(null);
  const chatTaskIdRef = useRef<string | null>(null);
  chatTaskIdRef.current = chatTaskId;
  const [projectTurnTaskId, setProjectTurnTaskId] = useState<string | null>(null);
  const projectTurnTaskIdRef = useRef<string | null>(null);
  projectTurnTaskIdRef.current = projectTurnTaskId;
  const [petTaskId, setPetTaskId] = useState<string | null>(null);
  const petSubmissionRef = useRef(false);
  const petConversationIdRef = useRef<string | null>(null);
  const activeActionCountRef = useRef(0);

  const {
    healthQuery,
    permissionsQuery,
    projectsQuery,
    conversationsQuery,
    providersQuery,
    providerHealthQuery,
  } = useWorkspaceBaseQueries(client);
  const permissionProfile = permissionsQuery.data?.profile ?? null;
  const modelController = useModelSelection(client, healthQuery.isSuccess);

  const projects = sortHistoryItems(projectsQuery.data?.items ?? []);
  const selectedProject = selectedItem(projects, projectSelection);
  const workspaceKnowledge = useWorkspaceKnowledge({
    client,
    enabled: healthQuery.isSuccess,
    mode,
    projectId: selectedProject?.id ?? null,
  });
  const allConversations = sortHistoryItems(conversationsQuery.data?.items ?? []);
  const conversations = allConversations.filter((conversation) => conversation.project_id === selectedProject?.id);
  const projectConversations = allConversations.filter(
    (conversation) => conversation.workspace_type === "project_chat",
  );
  const chatConversations = allConversations.filter(
    (conversation) => conversation.project_id === null && conversation.workspace_type === "chat_scratch",
  );
  const selectedConversation = conversationSelection === projectOverviewSelection
    ? null
    : selectedItem(conversations, conversationSelection);
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
    queryFn: () => collectCursorPages((cursor) => client.tasks.list({ limit: 100, cursor })),
    enabled: healthQuery.isSuccess,
    retry: false,
  });
  const allTasks = tasksQuery.data?.items ?? [];
  const tasks = allTasks.filter((task) => task.conversation_id === selectedConversation?.id);
  const requestedProjectTask = selectedItem(tasks, taskSelection);
  const chatTasks = allTasks.filter((task) => task.conversation_id === selectedChatConversation?.id);
  const latestProjectTask =
    tasks.find((task) => task.id === projectTurnTaskId) ??
    tasks.find((task) => task.id === selectedConversation?.active_task_id) ??
    [...tasks].sort((left, right) => right.updated_at.localeCompare(left.updated_at)).at(0) ??
    null;
  const projectWorkspaceTask = selectWorkspaceTask(
    tasks,
    projectTurnTaskId ?? selectedConversation?.active_task_id ?? null,
  );
  const selectedTask = requestedProjectTask ?? latestProjectTask;
  const workspaceTask =
    mode === "chat"
      ? selectWorkspaceTask(
        chatTasks,
        chatTaskId ?? selectedChatConversation?.active_task_id ?? null,
      )
      : projectWorkspaceTask;
  const messagesQuery = useQuery({
    queryKey: [...workspaceKey, "messages", selectedChatConversation?.id],
    queryFn: () =>
      client.messages.list({
        conversation_id: requireId(selectedChatConversation?.id),
        limit: 100,
      }),
    enabled: selectedChatConversation !== null,
    retry: false,
    staleTime: messageCacheStaleTime,
  });
  const messageItems = messagesQuery.data?.items ?? [];
  const persistedChatTurnId = latestPersistedTurnId(messageItems);
  const messages = messageItems.filter(
    (message) => developerMode || message.visibility === "user",
  );
  const realtimeTranscriptQuery = useQuery({
    queryKey: [...workspaceKey, "realtime-transcript", selectedChatConversation?.id],
    queryFn: () =>
      client.realtime.transcript.list({
        conversation_id: requireId(selectedChatConversation?.id),
        limit: 500,
      }),
    enabled: selectedChatConversation !== null,
    retry: false,
    staleTime: messageCacheStaleTime,
  });
  const realtimeTranscript = realtimeTranscriptQuery.data?.items ?? [];
  const projectMessagesQuery = useQuery({
    queryKey: [...workspaceKey, "project-messages", selectedConversation?.id],
    queryFn: () =>
      client.messages.list({
        conversation_id: requireId(selectedConversation?.id),
        limit: 100,
      }),
    enabled: mode === "project" && selectedConversation !== null,
    retry: false,
    staleTime: messageCacheStaleTime,
  });
  const projectMessageItems = projectMessagesQuery.data?.items ?? [];
  const persistedProjectTurnId = latestPersistedTurnId(projectMessageItems);
  const prefetchConversation = useConversationPrefetch(client);

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
  const previewQueryKey = [
    ...workspaceKey,
    "preview",
    workspaceTask?.conversation_id,
    workspaceTask?.id,
    workspaceTask?.target_version_id,
  ] as const;
  const previewQuery = useQuery({
    queryKey: previewQueryKey,
    queryFn: () =>
      client.previews.resolve({
        task_id: requireId(workspaceTask?.id),
        workspace_id: requireId(workspaceTask?.workspace_id),
        version_id: requireId(workspaceTask?.target_version_id),
      }),
    enabled: workspaceTask !== null && workspaceTask.target_version_id !== null && selectedWorkspaceQuery.isSuccess,
    retry: false,
  });
  const runtimeHealthQueryKey = [
    ...workspaceKey,
    "runtime-health",
    workspaceTask?.conversation_id,
    workspaceTask?.id,
  ] as const;
  const runtimeHealthQuery = useQuery({
    queryKey: runtimeHealthQueryKey,
    queryFn: () => client.runtimes.health(requireId(workspaceTask?.id)),
    enabled: workspaceTask !== null,
    retry: false,
  });
  const previewActivationState = usePreviewActivation({
    client: client.previews,
    enabled: healthQuery.isSuccess && selectedWorkspaceQuery.isSuccess,
    task: workspaceTask,
    workspace: selectedWorkspaceQuery.data ?? null,
    onActivated(activation) {
      if (activation.context !== null) {
        queryClient.setQueryData(previewQueryKey, activation.context);
      }
      void queryClient.invalidateQueries({ queryKey: runtimeHealthQueryKey, exact: true });
    },
  });
  const browserConversationId = workspaceTask?.conversation_id ??
    (mode === "chat" ? selectedChatConversation?.id : selectedConversation?.id) ?? null;
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

  const {
    queueEventInvalidation,
    invalidateDomains,
    invalidateHistory,
    invalidateAssistantScope,
  } = useWorkspaceInvalidation();

  const invalidateExecution = useCallback(
    () =>
      invalidateDomains(
        ["tasks", "workspace", "previews", "media", "approvals", "traces"],
        {
          conversationId:
            workspaceTask?.conversation_id ??
            (mode === "chat" ? selectedChatConversation?.id : selectedConversation?.id) ??
            null,
          projectId: selectedProject?.id ?? null,
          taskId:
            workspaceTask?.id ??
            (mode === "chat" ? chatTaskId : selectedTask?.id) ??
            null,
        },
      ),
    [
      chatTaskId,
      invalidateDomains,
      mode,
      selectedChatConversation?.id,
      selectedConversation?.id,
      selectedProject?.id,
      selectedTask?.id,
      workspaceTask?.conversation_id,
      workspaceTask?.id,
    ],
  );

  const chatAssistant = useAssistantTurn({
    client,
    conversationId: selectedChatConversation?.id ?? null,
    persistedTurnId: persistedChatTurnId,
    profileId: selectedProfileId,
    modelSelection: modelController.selection,
    operationMode: "answer",
    events: allEvents,
    onTaskCreated(taskId) {
      chatTaskIdRef.current = taskId;
      setChatTaskId(taskId);
      if (petSubmissionRef.current) setPetTaskId(taskId);
    },
    onSettled: () =>
      invalidateAssistantScope(selectedChatConversation?.id ?? null, chatTaskIdRef.current),
  });
  const projectAssistant = useAssistantTurn({
    client,
    conversationId: selectedConversation?.id ?? null,
    persistedTurnId: persistedProjectTurnId,
    profileId: selectedProfileId,
    modelSelection: modelController.selection,
    operationMode: "continue_current_chat_draft",
    events: allEvents,
    onTaskCreated(taskId) {
      projectTurnTaskIdRef.current = taskId;
      setProjectTurnTaskId(taskId);
      setTaskSelection(taskId);
    },
    onSettled() {
      const settledTaskId = projectTurnTaskIdRef.current ?? selectedTask?.id ?? null;
      setProjectTurnTaskId(null);
      void invalidateAssistantScope(selectedConversation?.id ?? null, settledTaskId);
    },
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
    setEventStreamError(null);
    setEventStreamErrorCode(null);
    void runResilientEventDelivery(
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
          queueEventInvalidation(event);
        },
        onError(error) {
          if (controller.signal.aborted) return;
          setEventStreamError(errorMessage(error));
          setEventStreamErrorCode(coreErrorCode(error));
        },
        onRecovered() {
          if (controller.signal.aborted) return;
          setEventStreamError(null);
          setEventStreamErrorCode(null);
        },
      },
    );
    return () => controller.abort();
  }, [client, healthQuery.isSuccess, queueEventInvalidation]);

  const runAction = useCallback(
    async <T>(operation: () => Promise<T>): Promise<T> => {
      activeActionCountRef.current += 1;
      setIsActing(true);
      setActionError(null);
      setActionErrorCode(null);
      try {
        return await operation();
      } catch (error) {
        setActionError(errorMessage(error));
        setActionErrorCode(coreErrorCode(error));
        throw error;
      } finally {
        activeActionCountRef.current = Math.max(0, activeActionCountRef.current - 1);
        setIsActing(activeActionCountRef.current > 0);
      }
    },
    [],
  );
  const assistantScheduling = useAssistantScheduling({
    client,
    enabled: healthQuery.isSuccess && mode === "chat",
    currentConversationId: selectedChatConversation?.id ?? null,
    selectedProfileId,
    modelSelection: modelController.selection,
    allConversations,
    allTasks,
    runAction,
    invalidateHistory,
    setMode,
    setProjectSelection,
    setConversationSelection,
    setChatConversationSelection,
    setTaskSelection,
    setChatTaskId,
  });
  const workspaceBrowser = useWorkspaceBrowser({
    client,
    enabled: healthQuery.isSuccess,
    conversationId: browserConversationId,
    projectId: selectedProject?.id ?? null,
    taskId: workspaceTask?.id ?? null,
    runAction,
  });

  const { setPermissionProfile, setCapabilityEnabled } = useWorkspacePermissions({
    client,
    current: permissionsQuery.data,
    capabilities: capabilitiesQuery.data,
    runAction,
    onConflict: (message, code) => {
      setActionError(message);
      setActionErrorCode(code);
    },
  });

  const selectProjectFolder = useCallback(
    () => runAction(() => client.projects.selectFolder()),
    [client.projects, runAction],
  );

  const historyActions = useMemo(
    () =>
      createWorkspaceHistoryActions({
        client,
        runAction,
        invalidateHistory,
        projects,
        selectedProjectId: selectedProject?.id ?? null,
        projectConversations,
        chatConversations,
        conversationSelection,
        chatConversationSelection,
        setProjectSelection,
        setConversationSelection,
        setChatConversationSelection,
        setTaskSelection,
        setChatTaskId,
        setPetTaskId,
        setPetConversationId: (value) => {
          petConversationIdRef.current = value;
        },
        setMode,
        resetChatAssistant: chatAssistant.reset,
      }),
    [
      chatAssistant.reset,
      chatConversationSelection,
      chatConversations,
      client,
      conversationSelection,
      projectConversations,
      projects,
      invalidateHistory,
      runAction,
      selectedProject?.id,
      setChatConversationSelection,
      setConversationSelection,
      setMode,
      setProjectSelection,
      setTaskSelection,
    ],
  );

  const actions = useMemo(
    () => ({
      ...historyActions,
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
          await invalidateHistory();
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
        await runAction(async () => {
          const latest = await client.tasks.get(task.id);
          return client.tasks.updateMetadata({
            task_id: task.id,
            display_title: title,
            expected_revision: latest.metadata_revision,
          });
        });
        await invalidateDomains(["tasks"], { taskId: task.id });
      },
      async setTaskPinned(task: Task, pinned: boolean) {
        await runAction(async () => {
          const latest = await client.tasks.get(task.id);
          return client.tasks.updateMetadata({
            task_id: task.id,
            pinned,
            expected_revision: latest.metadata_revision,
          });
        });
        await invalidateDomains(["tasks"], { taskId: task.id });
      },
      async archiveTask(task: Task) {
        await runAction(async () => {
          const latest = await client.tasks.get(task.id);
          return client.tasks.archive({
            task_id: task.id,
            expected_revision: latest.metadata_revision,
          });
        });
        await invalidateDomains(["tasks"], { taskId: task.id });
      },
      async decideApproval(approvalId: string, approved: boolean) {
        const result = await runAction(() =>
          client.approvals.decide({
            approval_id: approvalId,
            approved,
          }),
        );
        await invalidateDomains(
          ["messages", "approvals", "tasks", "traces"],
          {
            conversationId: workspaceTask?.conversation_id ?? null,
            taskId: workspaceTask?.id ?? selectedTask?.id ?? chatTaskId,
            turnId: result.assistant_turn_id,
          },
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
        const preview = previewQuery.data?.preview;
        if (workspace === undefined || versionId === null) {
          throw new Error("Workspace Version is unavailable");
        }
        await runAction(() =>
          client.previews.start({
            task_id: workspaceTask.id,
            workspace_id: workspaceTask.workspace_id,
            version_id: versionId,
            expected_workspace_revision: workspace.revision,
            idempotency_key: previewStartIdempotencyKey(workspaceTask.id, preview),
          }),
        );
        await invalidateDomains(
          ["previews"],
          { conversationId: workspaceTask.conversation_id, taskId: workspaceTask.id },
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
        await invalidateDomains(
          ["previews"],
          { conversationId: workspaceTask.conversation_id, taskId: workspaceTask.id },
        );
      },
      ...workspaceBrowser.actions,
      async reviewTask() {
        if (selectedTask === null) throw new Error("Task is unavailable");
        await runAction(() => client.tasks.review(selectedTask.id));
        await invalidateExecution();
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
        await invalidateExecution();
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
        await invalidateExecution();
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
        await invalidateDomains(
          ["knowledge"],
          { projectId: selectedProject?.id ?? null, taskId },
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
        await invalidateDomains(
          ["knowledge"],
          { projectId: selectedProject?.id ?? null, taskId },
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
        invalidateExecution,
        refreshFiles: () =>
          queryClient.invalidateQueries({
            queryKey: [
              ...workspaceKey,
              "files",
              workspaceTask?.conversation_id,
              workspaceTask?.workspace_id,
              workspaceTask?.target_version_id,
            ],
            exact: true,
          }),
      }),
    }),
    [
      chatAssistant,
      chatConversationSelection,
      chatTaskId,
      client,
      historyActions,
      previewQuery.data?.preview,
      projectAssistant,
      invalidateDomains,
      invalidateExecution,
      invalidateHistory,
      mode,
      runAction,
      selectedProject,
      selectedChatConversation,
      selectedConversation,
      selectedWorkspaceQuery.data,
      selectedTask,
      workspaceTask,
      workspaceBrowser.actions,
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
    modelController.error,
    tasksQuery.error,
    versionsQuery.error,
    approvalsQuery.error,
    chatApprovalsQuery.error,
    permissionsQuery.error,
    capabilitiesQuery.error,
  );
  const historyLoading =
    healthQuery.isSuccess &&
    (projectsQuery.isPending ||
      conversationsQuery.isPending ||
      tasksQuery.isPending);
  const isWorkspaceLoading =
    healthQuery.isPending ||
    (healthQuery.isSuccess && permissionsQuery.isPending) ||
    historyLoading;
  const state = healthQuery.isError
    ? "offline"
    : isWorkspaceLoading
      ? "loading"
      : projects.length === 0
        ? "empty"
        : "ready";
  const conversationContentState =
    selectedChatConversation === null
      ? "idle"
      : messagesQuery.data !== undefined
        ? "ready"
        : messagesQuery.isError
          ? "error"
          : "loading";
  const projectContentState =
    selectedConversation === null
      ? "idle"
      : projectMessagesQuery.data !== undefined
        ? "ready"
        : projectMessagesQuery.isError
          ? "error"
          : "loading";
  const displayError = workspaceDisplayError(
    { message: actionError, code: actionErrorCode },
    { message: eventStreamError, code: eventStreamErrorCode },
  );

  return {
    state,
    connectionState: healthQuery.isError
      ? "offline"
      : healthQuery.isSuccess
        ? "ready"
        : "starting",
    historyLoading,
    conversationContentState,
    projectContentState,
    mode,
    statusLabel: healthQuery.isError
      ? "Core offline"
      : healthQuery.isPending
        ? "Core starting"
        : "Core ready",
    errorMessage: queryError === null ? null : errorMessage(queryError),
    actionError: displayError.message,
    actionErrorCode: displayError.code,
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
    realtimeTranscript,
    providers,
    providerHealth,
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
    workspaceActivePreviewId: selectedWorkspaceQuery.data === undefined
      ? mode === "project"
        ? selectedConversation?.active_preview_id ?? selectedProject?.active_preview_id ?? null
        : selectedChatConversation?.active_preview_id ?? null
      : selectedWorkspaceQuery.data.active_preview_id,
    selectedVersion,
    preview: previewActivationState.activation?.context ?? previewQuery.data ?? null,
    previewActivation: previewActivationState.activation,
    previewActivationLoading: previewActivationState.loading,
    previewActivationError: previewActivationState.error,
    runtimeHealth: runtimeHealthQuery.data ?? null,
    browserHealth: workspaceBrowser.health,
    browserSession: workspaceBrowser.session,
    browserSnapshot: workspaceBrowser.snapshot,
    browserLoading: workspaceBrowser.loading,
    browserError: workspaceBrowser.error,
    workspaceFiles: workspaceFilesQuery.data?.items ?? [],
    workspaceGeneration: workspaceFilesQuery.data?.generation ?? 0,
    knowledgeOverview: workspaceKnowledge.knowledgeOverview,
    knowledgeItems: workspaceKnowledge.knowledgeItems,
    knowledgeGraph: workspaceKnowledge.knowledgeGraph,
    knowledgeLoading: workspaceKnowledge.knowledgeLoading,
    knowledgeError: workspaceKnowledge.knowledgeError,
    obsidianHealth: workspaceKnowledge.obsidianHealth,
    obsidianSources: workspaceKnowledge.obsidianSources,
    obsidianItems: workspaceKnowledge.obsidianItems,
    obsidianSourceProjections: workspaceKnowledge.obsidianSourceProjections,
    obsidianLoading: workspaceKnowledge.obsidianLoading,
    obsidianError: workspaceKnowledge.obsidianError,
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
    backgroundTasks: assistantScheduling.backgroundTasks,
    backgroundTasksLoading: assistantScheduling.backgroundTasksLoading,
    backgroundTasksReady: assistantScheduling.backgroundTasksReady,
    chatTimelineTarget: assistantScheduling.chatTimelineTarget,
    chatSchedules: assistantScheduling.chatSchedules,
    chatSchedulesLoading: assistantScheduling.chatSchedulesLoading,
    projectTurn: projectAssistant.turn,
    projectTrace,
    projectTraceState,
    projectBusy: projectAssistant.isBusy,
    projectError: projectAssistant.error,
    petTaskId,
    setMode,
    setPermissionProfile,
    setCapabilityEnabled,
    listDocuments: actions.listDocuments,
    searchDocuments: actions.searchDocuments,
    deleteDocument: actions.deleteDocument,
    searchMemory: actions.searchMemory,
    forgetMemory: actions.forgetMemory,
    setDeveloperMode,
    selectModel: (selectionMode, modelId) =>
      runAction(() => modelController.update({ mode: selectionMode, model_id: modelId })),
    refreshModelCatalog: () => runAction(modelController.refresh),
    selectProject: actions.selectProject,
    selectConversation: actions.selectConversation,
    selectChatConversation: actions.selectChatConversation,
    prefetchConversation,
    selectTask: setTaskSelection,
    createProject: actions.createProject,
    importProject: actions.importProject,
    selectProjectFolder,
    selectObsidianVault: () => client.obsidian.selectVault(),
    connectObsidianVault: async (selection, options) => {
      if (mode !== "project" || selectedProject === null) {
        throw new Error("Open a project before connecting a Vault");
      }
      await runAction(() => client.obsidian.createSource({
        project_id: selectedProject.id,
        display_name: selection.display_name,
        local_path_token: selection.local_path_token,
        read_scope: options.readScope,
        allowed_directories: options.allowedDirectories,
        whole_vault_confirmed: options.wholeVaultConfirmed,
        managed_directory: options.managedDirectory,
        mode: "read_only",
        idempotency_key: `obsidian:${selectedProject.id}:${selection.local_path_token}`,
      }));
      await invalidateDomains(["knowledge"], { projectId: selectedProject.id });
    },
    syncObsidianSource: async (source) => {
      if (
        mode !== "project" ||
        selectedProject === null ||
        source.project_id !== selectedProject.id
      ) {
        throw new Error("This Vault source is outside the active project");
      }
      await runAction(() => client.obsidian.sync({
        source_id: source.id,
        expected_revision: source.revision,
      }));
      await invalidateDomains(["knowledge"], { projectId: selectedProject.id });
    },
    readObsidianItem: (item, signal) => {
      if (
        mode !== "project" ||
        selectedProject === null ||
        !workspaceKnowledge.obsidianSources.some(
          (source) => source.id === item.source_id && source.project_id === selectedProject.id,
        )
      ) {
        throw new Error("This Vault item is outside the active project");
      }
      const sourceRevision = workspaceKnowledge.obsidianSourceProjections.find(
        (projection) => projection.sourceId === item.source_id,
      )?.sourceRevision;
      if (sourceRevision === null || sourceRevision === undefined) {
        throw new Error("This Vault source has not finished loading");
      }
      return client.obsidian.readItem({
        source_id: item.source_id,
        relative_path: item.relative_path,
        expected_source_revision: sourceRevision,
        expected_content_hash: item.content_hash,
      }, { signal });
    },
    createChatConversation: actions.createChatConversation,
    createPetChatConversation: actions.createPetChatConversation,
    createProjectConversation: actions.createProjectConversation,
    renameProject: actions.renameProject,
    setProjectPinned: actions.setProjectPinned,
    archiveProject: actions.archiveProject,
    deleteProject: actions.deleteProject,
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
    pauseChatTurn: chatAssistant.pauseWorkflow,
    resumeChatTurn: chatAssistant.resumeWorkflow,
    steerChatTurn: chatAssistant.steer,
    respondToChatClarification: chatAssistant.respondToClarification,
    retryChatTurn: chatAssistant.retry,
    manageBackgroundTask: assistantScheduling.manageBackgroundTask,
    openBackgroundTask: assistantScheduling.openBackgroundTask,
    openBackgroundTaskLocation: assistantScheduling.openBackgroundTaskLocation,
    clearChatTimelineTarget: assistantScheduling.clearChatTimelineTarget,
    createChatSchedule: assistantScheduling.createChatSchedule,
    updateChatSchedule: assistantScheduling.updateChatSchedule,
    pauseChatSchedule: assistantScheduling.pauseChatSchedule,
    resumeChatSchedule: assistantScheduling.resumeChatSchedule,
    runNowChatSchedule: assistantScheduling.runNowChatSchedule,
    cancelChatSchedule: assistantScheduling.cancelChatSchedule,
    retryPendingChatMessage: chatAssistant.retryPending,
    deletePendingChatMessage: chatAssistant.deletePending,
    takePendingChatMessageForEdit: chatAssistant.takePendingForEdit,
    copyMessage: actions.copyMessage,
    openMessageLink: actions.openMessageLink,
    readWorkspaceFile: actions.readWorkspaceFile,
    readWorkspaceSource: actions.readWorkspaceSource,
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
    cancelMediaJob: async (job) => {
      await runAction(() =>
        client.media.videos.cancel({
          job_id: job.id,
          expected_revision: job.revision,
          idempotency_key: `desktop:media-cancel:${job.id}:${job.revision}`,
          user_confirmed: true,
        }),
      );
      await invalidateDomains(
        ["media"],
        {
          conversationId: workspaceTask?.conversation_id ?? null,
          taskId: workspaceTask?.id ?? null,
        },
      );
    },
    cancelProjectTurn: projectAssistant.cancel,
    pauseProjectTurn: projectAssistant.pauseWorkflow,
    resumeProjectTurn: projectAssistant.resumeWorkflow,
    steerProjectTurn: projectAssistant.steer,
    decideApproval: actions.decideApproval,
    startPreview: actions.startPreview,
    stopPreview: actions.stopPreview,
    startBrowser: actions.startBrowser,
    stopBrowser: actions.stopBrowser,
    navigateBrowser: actions.navigateBrowser,
    openBrowserTab: actions.openBrowserTab,
    selectBrowserTab: actions.selectBrowserTab,
    closeBrowserTab: actions.closeBrowserTab,
    executeBrowserAction: actions.executeBrowserAction,
    refreshBrowser: actions.refreshBrowser,
    setBrowserSurfaceActive: actions.setBrowserSurfaceActive,
    reviewTask: actions.reviewTask,
    acceptVersion: actions.acceptVersion,
    discardVersion: actions.discardVersion,
    retryWorkspace: async () => {
      setActionError(null);
      setActionErrorCode(null);
      await queryClient.resetQueries({ queryKey: workspaceKey });
    },
    openSettings: (category) => client.desktop.openSettings(category),
  };
}

function latestPersistedTurnId(messages: Message[]): string | null {
  return [...messages]
    .sort((left, right) => right.sequence - left.sequence)
    .find((message) => message.turn_id !== null)?.turn_id ?? null;
}
