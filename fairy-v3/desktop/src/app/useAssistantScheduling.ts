import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";

import type { ScheduleRuleDraft } from "../chat/scheduleRules";
import type { ChatTimelineTarget } from "../chat/timelineTarget";
import type {
  AssistantBackgroundTask,
  AssistantBackgroundTaskPage,
  AssistantSchedule,
  Conversation,
  ModelSelectionPreference,
  Task,
} from "../core/client";
import type {
  BackgroundTaskAction,
  WorkspaceClient,
  WorkspaceMode,
} from "./workspaceTypes";
import { requireId, workspaceKey } from "./workspaceModelUtils";

type RunAction = <T>(operation: () => Promise<T>) => Promise<T>;
type SelectionSetter = (value: string | null) => void;

interface AssistantSchedulingInput {
  client: WorkspaceClient;
  enabled: boolean;
  currentConversationId: string | null;
  selectedProfileId: string | null;
  modelSelection: ModelSelectionPreference | null;
  allConversations: Conversation[];
  allTasks: Task[];
  runAction: RunAction;
  invalidateHistory(): Promise<void>;
  setMode(value: WorkspaceMode): void;
  setProjectSelection: SelectionSetter;
  setConversationSelection: SelectionSetter;
  setChatConversationSelection: SelectionSetter;
  setTaskSelection: SelectionSetter;
  setChatTaskId: SelectionSetter;
}

const EMPTY_BACKGROUND_TASKS: AssistantBackgroundTaskPage = {
  current: [],
  other: [],
  recent: [],
  nonterminal_count: 0,
};

export function useAssistantScheduling({
  client,
  enabled,
  currentConversationId,
  selectedProfileId,
  modelSelection,
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
}: AssistantSchedulingInput) {
  const queryClient = useQueryClient();
  const [chatTimelineTarget, setChatTimelineTarget] = useState<ChatTimelineTarget | null>(null);
  const [pendingLocation, setPendingLocation] = useState<{
    conversationId: string;
    turnId: string | null;
  } | null>(null);
  const backgroundTasksQuery = useQuery({
    queryKey: [...workspaceKey, "background-tasks", currentConversationId],
    queryFn: () => client.assistant.backgroundTasks.list(currentConversationId),
    enabled,
    retry: false,
    refetchInterval: 2_500,
  });
  const chatSchedulesQuery = useQuery({
    queryKey: [...workspaceKey, "assistant-schedules", currentConversationId],
    queryFn: () => client.assistant.schedules.list(requireId(currentConversationId)),
    enabled: enabled && currentConversationId !== null,
    retry: false,
    refetchInterval: 2_500,
  });

  const refresh = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: [...workspaceKey, "assistant-schedules"] }),
      queryClient.invalidateQueries({ queryKey: [...workspaceKey, "background-tasks"] }),
    ]);
  }, [queryClient]);

  const manageBackgroundTask = useCallback(
    async (task: AssistantBackgroundTask, action: BackgroundTaskAction): Promise<void> => {
      await runAction(async () => {
        if (task.turn_id !== null) {
          if (action === "pause") await client.assistant.turns.pause(task.turn_id);
          else if (action === "resume") await client.assistant.turns.resume(task.turn_id);
          else if (action === "cancel") {
            if (task.turn_cancellation_revision === null) {
              throw new Error("Background task cancellation revision is unavailable");
            }
            await client.assistant.turns.cancel({
              turn_id: task.turn_id,
              expected_cancellation_revision: task.turn_cancellation_revision,
            });
          } else {
            throw new Error("A running task cannot be started again");
          }
          return;
        }
        if (task.schedule_id === null || task.schedule_revision === null) {
          throw new Error("Background schedule binding is unavailable");
        }
        if (action === "pause") {
          await client.assistant.schedules.pause(task.schedule_id, task.schedule_revision);
        } else if (action === "resume") {
          await client.assistant.schedules.resume(task.schedule_id, task.schedule_revision);
        } else if (action === "cancel") {
          await client.assistant.schedules.cancel(task.schedule_id, task.schedule_revision);
        } else {
          await client.assistant.schedules.runNow(
            task.schedule_id,
            task.schedule_revision,
            `background-run-now:${task.schedule_id}:${crypto.randomUUID()}`,
          );
        }
      });
      await refresh();
    },
    [client.assistant, refresh, runAction],
  );

  const createChatSchedule = useCallback(
    async (instruction: string, rule: ScheduleRuleDraft): Promise<void> => {
      const conversationId = requireId(currentConversationId);
      await runAction(() => client.assistant.schedules.create({
        conversation_id: conversationId,
        instruction,
        operation_mode: "answer",
        ...rule,
        ...(modelSelection === null
          ? { profile_id: requireId(selectedProfileId) }
          : {
              model_selection: {
                mode: modelSelection.mode,
                model_id: modelSelection.model_id,
                revision: modelSelection.revision,
              },
            }),
        idempotency_key: `chat-schedule:${conversationId}:${crypto.randomUUID()}`,
      }));
      await refresh();
    },
    [client.assistant.schedules, currentConversationId, modelSelection, refresh, runAction, selectedProfileId],
  );

  const updateChatSchedule = useCallback(
    async (schedule: AssistantSchedule, rule: ScheduleRuleDraft): Promise<void> => {
      await runAction(() => client.assistant.schedules.update({
        schedule_id: schedule.id,
        expected_revision: schedule.active_revision,
        instruction: schedule.instruction,
        operation_mode: schedule.operation_mode,
        ...rule,
      }));
      await refresh();
    },
    [client.assistant.schedules, refresh, runAction],
  );

  const mutateChatSchedule = useCallback(
    async (
      schedule: AssistantSchedule,
      action: "pause" | "resume" | "cancel" | "run_now",
    ): Promise<void> => {
      await runAction(async () => {
        if (action === "pause") {
          await client.assistant.schedules.pause(schedule.id, schedule.active_revision);
        } else if (action === "resume") {
          await client.assistant.schedules.resume(schedule.id, schedule.active_revision);
        } else if (action === "cancel") {
          await client.assistant.schedules.cancel(schedule.id, schedule.active_revision);
        } else {
          await client.assistant.schedules.runNow(
            schedule.id,
            schedule.active_revision,
            `chat-schedule-run-now:${schedule.id}:${crypto.randomUUID()}`,
          );
        }
      });
      await refresh();
    },
    [client.assistant.schedules, refresh, runAction],
  );

  const openBackgroundTask = useCallback((task: AssistantBackgroundTask) => {
    if (task.project_id === null) {
      setChatTimelineTarget({
        key: crypto.randomUUID(),
        scheduleId: task.schedule_id,
        turnId: task.turn_id,
      });
      setMode("chat");
      setChatConversationSelection(task.conversation_id);
      setChatTaskId(task.task_id);
      return;
    }
    setMode("project");
    setProjectSelection(task.project_id);
    setConversationSelection(task.conversation_id);
    setTaskSelection(task.task_id);
  }, [
    setChatConversationSelection,
    setChatTaskId,
    setConversationSelection,
    setMode,
    setProjectSelection,
    setTaskSelection,
  ]);

  const openBackgroundTaskLocation = useCallback(
    (conversationId: string, turnId: string | null) => {
      setPendingLocation({ conversationId, turnId });
      void invalidateHistory();
    },
    [invalidateHistory],
  );

  useEffect(() => {
    if (pendingLocation === null) return;
    const { conversationId, turnId } = pendingLocation;
    const conversation = allConversations.find((item) => item.id === conversationId);
    if (conversation === undefined) return;
    const conversationTasks = allTasks.filter((task) => task.conversation_id === conversationId);
    const targetTask = conversationTasks.find(
      (task) => task.id === conversation.active_task_id,
    ) ?? [...conversationTasks]
      .sort((left, right) => right.updated_at.localeCompare(left.updated_at))[0] ?? null;
    if (conversation.project_id !== null) {
      setMode("project");
      setProjectSelection(conversation.project_id);
      setConversationSelection(conversation.id);
      setTaskSelection(targetTask?.id ?? null);
      setPendingLocation(null);
      return;
    }
    setChatTimelineTarget({ key: crypto.randomUUID(), scheduleId: null, turnId });
    setMode("chat");
    setChatConversationSelection(conversation.id);
    setChatTaskId(targetTask?.id ?? null);
    setPendingLocation(null);
  }, [
    allConversations,
    allTasks,
    pendingLocation,
    setChatConversationSelection,
    setChatTaskId,
    setConversationSelection,
    setMode,
    setProjectSelection,
    setTaskSelection,
  ]);

  return {
    backgroundTasks: backgroundTasksQuery.data ?? EMPTY_BACKGROUND_TASKS,
    backgroundTasksLoading: backgroundTasksQuery.isPending && backgroundTasksQuery.isEnabled,
    backgroundTasksReady: backgroundTasksQuery.isSuccess,
    chatSchedules: chatSchedulesQuery.data?.items ?? [],
    chatSchedulesLoading: chatSchedulesQuery.isPending && chatSchedulesQuery.isEnabled,
    chatTimelineTarget,
    manageBackgroundTask,
    openBackgroundTask,
    openBackgroundTaskLocation,
    clearChatTimelineTarget: (key: string) => {
      setChatTimelineTarget((current) => current?.key === key ? null : current);
    },
    createChatSchedule,
    updateChatSchedule,
    pauseChatSchedule: (schedule: AssistantSchedule) => mutateChatSchedule(schedule, "pause"),
    resumeChatSchedule: (schedule: AssistantSchedule) => mutateChatSchedule(schedule, "resume"),
    runNowChatSchedule: (schedule: AssistantSchedule) => mutateChatSchedule(schedule, "run_now"),
    cancelChatSchedule: (schedule: AssistantSchedule) => mutateChatSchedule(schedule, "cancel"),
  };
}
