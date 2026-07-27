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
  type PresenceAmbientDialogue,
  type PresenceReply,
  type PresenceProjectionState,
} from "./domain/projection";
import type { RealtimePresenceState } from "../realtime/realtimePresence";

interface PresenceBridgeProps {
  events: readonly EventEnvelope[];
  reply?: PresenceReply | null;
  ambientDialogue?: PresenceAmbientDialogue | null;
  speaking?: boolean;
  realtimePresence?: RealtimePresenceState;
  onNewChat?(): void | Promise<void>;
  onSend?(text: string): void | Promise<void>;
  onCancel?(): void | Promise<void>;
  onStopVoice?(): void;
  onInputState?(state: { open: boolean; focused: boolean }): void;
  onAmbientVisibilityChange?(dialogue: PresenceAmbientDialogue | null): void;
  channelFactory?: () => PresenceChannel;
}

export function PresenceBridge({
  events,
  reply = null,
  ambientDialogue = null,
  speaking = false,
  realtimePresence = "idle",
  onNewChat,
  onSend,
  onCancel,
  onStopVoice,
  onInputState,
  onAmbientVisibilityChange,
  channelFactory = createPresenceChannel,
}: PresenceBridgeProps) {
  const projection = useMemo(() => {
    const durable = [...events]
        .sort((left, right) => left.cursor - right.cursor)
        .reduce(PresenceProjection.reduce, PresenceProjection.initial());
    return withEphemeralPresence(
      durable,
      reply,
      ambientDialogue,
      speaking,
      realtimePresence,
    );
  }, [ambientDialogue, events, reply, speaking, realtimePresence]);
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
        onInputState,
      );
    });
    channel.publishProjection(projectionRef.current);
    return () => {
      stopRequests();
      channel.close();
      channelRef.current = null;
    };
  }, [
    channelFactory,
    onCancel,
    onInputState,
    onNewChat,
    onSend,
    onStopVoice,
  ]);

  useEffect(() => {
    channelRef.current?.publishProjection(projection);
  }, [projection]);

  useEffect(() => {
    onAmbientVisibilityChange?.(projection.ambient_dialogue);
  }, [onAmbientVisibilityChange, projection.ambient_dialogue]);

  return null;
}

function withEphemeralPresence(
  projection: PresenceProjectionState,
  reply: PresenceReply | null,
  ambientDialogue: PresenceAmbientDialogue | null,
  speaking: boolean,
  realtimePresence: RealtimePresenceState,
): PresenceProjectionState {
  const realtimeWorkState = realtimePresenceWorkState(realtimePresence);
  const effectiveReply = projection.notice === null && reply !== null ? reply : projection.reply;
  const ambientBlocked =
    projection.notice !== null ||
    effectiveReply !== null ||
    realtimeWorkState !== null ||
    !["idle", "ready"].includes(projection.work_state);
  return {
    ...projection,
    reply: effectiveReply,
    ambient_dialogue: ambientBlocked ? null : ambientDialogue,
    work_state: realtimeWorkState ?? projection.work_state,
    status_text: realtimeStatusText(realtimePresence) ?? projection.status_text,
    speaking: realtimePresence === "speaking" || speaking,
  };
}

function realtimePresenceWorkState(
  state: RealtimePresenceState,
): PresenceProjectionState["work_state"] | null {
  if (
    state === "preparing"
    || state === "loading_model"
    || state === "connecting"
    || state === "observing"
    || state === "thinking"
    || state === "searching"
  ) return "analyzing";
  if (state === "listening" || state === "standby" || state === "privacy_paused") return "ready";
  if (state === "speaking") return "streaming";
  if (state === "resource_limited" || state === "error") return "error";
  return null;
}

function realtimeStatusText(state: RealtimePresenceState): string | null {
  if (state === "preparing") return "Fairy is preparing";
  if (state === "loading_model") return "Fairy is loading the local model";
  if (state === "connecting") return "Fairy is connecting";
  if (state === "listening") return "Fairy is listening";
  if (state === "observing") return "Fairy is observing";
  if (state === "thinking") return "Fairy is thinking";
  if (state === "searching") return "Fairy is searching";
  if (state === "speaking") return "Fairy is speaking";
  if (state === "standby") return "Fairy is standing by";
  if (state === "privacy_paused") return "Realtime privacy pause is active";
  if (state === "resource_limited") return "Realtime resources are limited";
  if (state === "error") return "Realtime companion needs attention";
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
  onInputState: ((state: { open: boolean; focused: boolean }) => void) | undefined,
): void {
  switch (request.kind) {
    case "projection":
      channel.publishProjection(projection.current);
      break;
    case "workspace.open":
      void openWorkspaceWindow();
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
    case "input.state":
      onInputState?.({ open: request.open, focused: request.focused });
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
