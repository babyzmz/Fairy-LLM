import { useEffect, useMemo, useRef } from "react";

import type { EventEnvelope } from "../core/contracts";

import {
  createPresenceChannel,
  type PresenceChannel,
  type PresenceRequest,
  type PresenceSubmissionFailure,
} from "./transport/presenceChannel";
import {
  PresenceProjection,
  type PresenceReply,
  type PresenceProjectionState,
} from "./domain/projection";
import type { RealtimePresenceState } from "../realtime/RealtimeCompanion";

interface PresenceBridgeProps {
  events: readonly EventEnvelope[];
  reply?: PresenceReply | null;
  speaking?: boolean;
  realtimePresence?: RealtimePresenceState;
  onNewChat?(): void | Promise<void>;
  onSend?(text: string): void | Promise<void>;
  onCancel?(): void | Promise<void>;
  onStopVoice?(): void;
  onOpenRealtime?(): void;
  channelFactory?: () => PresenceChannel;
}

export function PresenceBridge({
  events,
  reply = null,
  speaking = false,
  realtimePresence = "idle",
  onNewChat,
  onSend,
  onCancel,
  onStopVoice,
  onOpenRealtime,
  channelFactory = createPresenceChannel,
}: PresenceBridgeProps) {
  const projection = useMemo(() => {
    const durable = [...events]
        .sort((left, right) => left.cursor - right.cursor)
        .reduce(PresenceProjection.reduce, PresenceProjection.initial());
    return withEphemeralPresence(durable, reply, speaking, realtimePresence);
  }, [events, reply, speaking, realtimePresence]);
  const projectionRef = useRef(projection);
  const channelRef = useRef<PresenceChannel | null>(null);
  projectionRef.current = projection;

  useEffect(() => {
    const channel = channelFactory();
    let actionQueue = Promise.resolve();
    const enqueue = (action: () => void | Promise<void>) => {
      actionQueue = actionQueue.catch(() => undefined).then(action);
    };
    channelRef.current = channel;
    const stopRequests = channel.onRequest((request) => {
      handleRequest(
        request,
        channel,
        projectionRef,
        enqueue,
        onNewChat,
        onSend,
        onCancel,
        onStopVoice,
        onOpenRealtime,
      );
    });
    channel.publishProjection(projectionRef.current);
    return () => {
      stopRequests();
      channel.close();
      channelRef.current = null;
    };
  }, [channelFactory, onCancel, onNewChat, onOpenRealtime, onSend, onStopVoice]);

  useEffect(() => {
    channelRef.current?.publishProjection(projection);
  }, [projection]);

  return null;
}

function withEphemeralPresence(
  projection: PresenceProjectionState,
  reply: PresenceReply | null,
  speaking: boolean,
  realtimePresence: RealtimePresenceState,
): PresenceProjectionState {
  const realtimeWorkState = realtimePresenceWorkState(realtimePresence);
  return {
    ...projection,
    reply: projection.notice === null && reply !== null ? reply : projection.reply,
    work_state: realtimeWorkState ?? projection.work_state,
    status_text: realtimeStatusText(realtimePresence) ?? projection.status_text,
    speaking: realtimePresence === "speaking" || speaking,
  };
}

function realtimePresenceWorkState(
  state: RealtimePresenceState,
): PresenceProjectionState["work_state"] | null {
  if (state === "starting" || state === "connecting" || state === "analyzing") return "analyzing";
  if (state === "active" || state === "listening") return "ready";
  if (state === "speaking") return "streaming";
  if (state === "stopping") return "analyzing";
  if (state === "completed") return "ready";
  if (state === "error") return "error";
  return null;
}

function realtimeStatusText(state: RealtimePresenceState): string | null {
  if (state === "starting" || state === "connecting") return "Fairy is connecting";
  if (state === "active" || state === "listening") return "Fairy is listening";
  if (state === "analyzing") return "Fairy is watching the game";
  if (state === "speaking") return "Fairy is speaking";
  if (state === "stopping") return "Fairy is wrapping up";
  if (state === "completed") return "Game session complete";
  if (state === "error") return "Game companion needs attention";
  return null;
}

function handleRequest(
  request: PresenceRequest,
  channel: PresenceChannel,
  projection: { current: PresenceProjectionState },
  enqueue: (action: () => void | Promise<void>) => void,
  onNewChat: (() => void | Promise<void>) | undefined,
  onSend: ((text: string) => void | Promise<void>) | undefined,
  onCancel: (() => void | Promise<void>) | undefined,
  onStopVoice: (() => void) | undefined,
  onOpenRealtime: (() => void) | undefined,
): void {
  switch (request.kind) {
    case "projection":
      channel.publishProjection(projection.current);
      break;
    case "workspace.open":
      void openWorkspaceWindow();
      break;
    case "realtime.open":
      void openWorkspaceWindow();
      onOpenRealtime?.();
      break;
    case "chat.new":
      if (onNewChat !== undefined) enqueue(onNewChat);
      break;
    case "chat.send":
      enqueue(async () => {
        if (onSend === undefined) {
          channel.publishSubmission(failedSubmission(request.submission_id, "unavailable"));
          return;
        }
        try {
          await onSend(request.text);
          channel.publishSubmission({
            submission_id: request.submission_id,
            status: "accepted",
            failure: null,
          });
        } catch (error) {
          channel.publishSubmission(
            failedSubmission(request.submission_id, publicFailure(error)),
          );
        }
      });
      break;
    case "chat.cancel":
      enqueue(async () => {
        if (onCancel === undefined) {
          channel.publishSubmission(failedSubmission(request.submission_id, "unavailable"));
          return;
        }
        try {
          await onCancel();
          channel.publishSubmission({
            submission_id: request.submission_id,
            status: "cancelled",
            failure: null,
          });
        } catch (error) {
          channel.publishSubmission(
            failedSubmission(request.submission_id, publicFailure(error)),
          );
        }
      });
      break;
    case "voice.stop":
      onStopVoice?.();
      break;
  }
}

function failedSubmission(
  submissionId: string,
  failure: PresenceSubmissionFailure,
) {
  return {
    submission_id: submissionId,
    status: "failed" as const,
    failure,
  };
}

function publicFailure(error: unknown): PresenceSubmissionFailure {
  const message = error instanceof Error ? error.message.toLowerCase() : "";
  if (
    message.includes("offline") ||
    message.includes("network") ||
    message.includes("fetch") ||
    message.includes("connection")
  ) {
    return "offline";
  }
  if (message.includes("already running") || message.includes("busy")) return "busy";
  return "unavailable";
}

async function openWorkspaceWindow(): Promise<void> {
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    const current = getCurrentWindow();
    if (!(await current.isVisible())) await current.show();
    await current.setFocus();
  } catch {
    // Browser previews do not expose native window controls.
  }
}
