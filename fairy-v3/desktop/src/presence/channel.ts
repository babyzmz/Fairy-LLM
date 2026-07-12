import { z } from "zod";

import {
  type PresenceProjectionState,
  presenceProjectionStateSchema,
} from "./projection";

export type PresenceRequest =
  | { kind: "projection" }
  | { kind: "workspace.open" }
  | { kind: "chat.new" }
  | { kind: "chat.send"; text: string }
  | { kind: "voice.stop" };

export interface PresenceChannel {
  publishProjection(projection: PresenceProjectionState): void;
  requestProjection(): void;
  requestWorkspaceOpen(): void;
  requestNewChat(): void;
  requestChatSend(text: string): void;
  requestVoiceStop(): void;
  onProjection(listener: (projection: PresenceProjectionState) => void): () => void;
  onRequest(listener: (request: PresenceRequest) => void): () => void;
  close(): void;
}

const requestSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("projection") }).strict(),
  z.object({ kind: z.literal("workspace.open") }).strict(),
  z.object({ kind: z.literal("chat.new") }).strict(),
  z.object({ kind: z.literal("chat.send"), text: z.string().trim().min(1).max(4_000) }).strict(),
  z.object({ kind: z.literal("voice.stop") }).strict(),
]);

const messageSchema = z.discriminatedUnion("kind", [
  z
    .object({ kind: z.literal("presence.projection"), projection: presenceProjectionStateSchema })
    .strict(),
  z
    .object({ kind: z.literal("presence.request"), request: requestSchema })
    .strict(),
]);

export function createPresenceChannel(): PresenceChannel {
  const projectionListeners = new Set<(value: PresenceProjectionState) => void>();
  const requestListeners = new Set<(value: PresenceRequest) => void>();
  const broadcast =
    typeof window !== "undefined" && "BroadcastChannel" in window
      ? new window.BroadcastChannel("fairy.presence.v2")
      : null;

  if (broadcast !== null) {
    broadcast.onmessage = (event: MessageEvent<unknown>) => {
      const parsed = messageSchema.safeParse(event.data);
      if (!parsed.success) return;
      if (parsed.data.kind === "presence.projection") {
        for (const listener of projectionListeners) listener(parsed.data.projection);
      } else {
        for (const listener of requestListeners) listener(parsed.data.request);
      }
    };
  }

  const postRequest = (request: PresenceRequest) => {
    const parsed = requestSchema.safeParse(request);
    if (parsed.success) {
      broadcast?.postMessage({ kind: "presence.request", request: parsed.data });
    }
  };

  return {
    publishProjection(projection) {
      const parsed = presenceProjectionStateSchema.safeParse(projection);
      if (parsed.success) {
        broadcast?.postMessage({
          kind: "presence.projection",
          projection: parsed.data,
        });
      }
    },
    requestProjection: () => postRequest({ kind: "projection" }),
    requestWorkspaceOpen: () => postRequest({ kind: "workspace.open" }),
    requestNewChat: () => postRequest({ kind: "chat.new" }),
    requestChatSend: (text) => postRequest({ kind: "chat.send", text }),
    requestVoiceStop: () => postRequest({ kind: "voice.stop" }),
    onProjection(listener) {
      projectionListeners.add(listener);
      return () => projectionListeners.delete(listener);
    },
    onRequest(listener) {
      requestListeners.add(listener);
      return () => requestListeners.delete(listener);
    },
    close() {
      projectionListeners.clear();
      requestListeners.clear();
      broadcast?.close();
    },
  };
}
