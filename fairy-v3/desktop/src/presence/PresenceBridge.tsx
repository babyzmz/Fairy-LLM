import { useEffect, useMemo, useRef } from "react";

import type { EventEnvelope } from "../core/contracts";

import {
  createPresenceChannel,
  type PresenceChannel,
  type PresenceRequest,
} from "./channel";
import {
  PresenceProjection,
  type PresenceReply,
  type PresenceProjectionState,
} from "./projection";

interface PresenceBridgeProps {
  events: readonly EventEnvelope[];
  reply?: PresenceReply | null;
  speaking?: boolean;
  onNewChat?(): void | Promise<void>;
  onSend?(text: string): void | Promise<void>;
  onStopVoice?(): void;
  channelFactory?: () => PresenceChannel;
}

export function PresenceBridge({
  events,
  reply = null,
  speaking = false,
  onNewChat,
  onSend,
  onStopVoice,
  channelFactory = createPresenceChannel,
}: PresenceBridgeProps) {
  const projection = useMemo(() => {
    const durable = [...events]
        .sort((left, right) => left.cursor - right.cursor)
        .reduce(PresenceProjection.reduce, PresenceProjection.initial());
    return withEphemeralPresence(durable, reply, speaking);
  }, [events, reply, speaking]);
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
        onStopVoice,
      );
    });
    channel.publishProjection(projectionRef.current);
    return () => {
      stopRequests();
      channel.close();
      channelRef.current = null;
    };
  }, [channelFactory, onNewChat, onSend, onStopVoice]);

  useEffect(() => {
    channelRef.current?.publishProjection(projection);
  }, [projection]);

  return null;
}

function withEphemeralPresence(
  projection: PresenceProjectionState,
  reply: PresenceReply | null,
  speaking: boolean,
): PresenceProjectionState {
  return {
    ...projection,
    reply: projection.notice === null && reply !== null ? reply : projection.reply,
    speaking,
  };
}

function handleRequest(
  request: PresenceRequest,
  channel: PresenceChannel,
  projection: { current: PresenceProjectionState },
  enqueue: (action: () => void | Promise<void>) => void,
  onNewChat: (() => void | Promise<void>) | undefined,
  onSend: ((text: string) => void | Promise<void>) | undefined,
  onStopVoice: (() => void) | undefined,
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
      if (onSend !== undefined) enqueue(() => onSend(request.text));
      break;
    case "voice.stop":
      onStopVoice?.();
      break;
  }
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
