import { useEffect, useRef } from "react";

import type {
  AssistantBackgroundTask,
  AssistantBackgroundTaskPage,
} from "../core/client";
import type { InvokeFunction } from "../core/tauriTransport";

export interface BackgroundTaskNotificationInput {
  title: string;
  body: string;
  conversation_id: string;
  turn_id: string | null;
}

export interface BackgroundTaskNotificationHost {
  notify(input: BackgroundTaskNotificationInput): Promise<void>;
}

export class TauriBackgroundTaskNotificationHost implements BackgroundTaskNotificationHost {
  constructor(private readonly invoke: InvokeFunction) {}

  notify(input: BackgroundTaskNotificationInput): Promise<void> {
    return this.invoke("background_task_notify", { input });
  }
}

export class BackgroundTaskNotificationTracker {
  private initialized = false;
  private readonly signatures = new Map<string, string>();

  observe(
    page: AssistantBackgroundTaskPage,
    suppress: (task: AssistantBackgroundTask) => boolean,
  ): BackgroundTaskNotificationInput[] {
    const items = allTasks(page);
    if (!this.initialized) {
      for (const item of items) this.signatures.set(item.id, signature(item));
      this.initialized = true;
      return [];
    }
    const notifications: BackgroundTaskNotificationInput[] = [];
    for (const item of items) {
      const nextSignature = signature(item);
      const previous = this.signatures.get(item.id);
      this.signatures.set(item.id, nextSignature);
      if (previous === nextSignature || !attentionStatus(item) || suppress(item)) continue;
      notifications.push(notificationInput(item));
    }
    return notifications;
  }
}

export function useBackgroundTaskNotifications({
  page,
  ready,
  host,
  workspaceVisible,
  selectedConversationId,
}: {
  page: AssistantBackgroundTaskPage;
  ready: boolean;
  host?: BackgroundTaskNotificationHost;
  workspaceVisible: boolean;
  selectedConversationId: string | null;
}) {
  const tracker = useRef(new BackgroundTaskNotificationTracker());
  useEffect(() => {
    if (!ready) return;
    const notifications = tracker.current.observe(page, (task) => (
      workspaceVisible &&
      document.visibilityState === "visible" &&
      document.hasFocus() &&
      task.conversation_id === selectedConversationId
    ));
    if (host === undefined) return;
    for (const notification of notifications) {
      void host.notify(notification).catch(() => undefined);
    }
  }, [host, page, ready, selectedConversationId, workspaceVisible]);
}

function allTasks(page: AssistantBackgroundTaskPage): AssistantBackgroundTask[] {
  return [...page.current, ...page.other, ...page.recent];
}

function signature(task: AssistantBackgroundTask): string {
  return `${task.status}\u0000${task.attention_code ?? ""}`;
}

function attentionStatus(task: AssistantBackgroundTask): boolean {
  return task.status === "completed" ||
    task.status === "failed" ||
    task.status === "waiting_for_approval" ||
    task.status === "attention_required";
}

function notificationInput(task: AssistantBackgroundTask): BackgroundTaskNotificationInput {
  const title = task.status === "completed"
    ? "Background task completed"
    : task.status === "failed"
      ? "Background task failed"
      : task.status === "waiting_for_approval"
        ? "Fairy needs approval"
        : "Scheduled task paused";
  const detail = task.public_error ?? (
    task.attention_code === null
      ? task.title
      : task.attention_code.replaceAll("_", " ").toLowerCase()
  );
  return {
    title,
    body: publicText(`${task.conversation_title} · ${detail}`, 240),
    conversation_id: task.conversation_id,
    turn_id: task.turn_id,
  };
}

function publicText(value: string, limit: number): string {
  return value.replace(/\s+/gu, " ").trim().slice(0, limit);
}
