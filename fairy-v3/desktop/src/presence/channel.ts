import { z } from "zod";

import {
  type PresenceProjectionState,
  presenceProjectionStateSchema,
} from "./projection";

export type PresenceRequest = "projection" | "workspace.toggle";

export interface PresenceChannel {
  publishProjection(projection: PresenceProjectionState): void;
  requestProjection(): void;
  requestWorkspaceToggle(): void;
  onProjection(listener: (projection: PresenceProjectionState) => void): () => void;
  onRequest(listener: (request: PresenceRequest) => void): () => void;
  close(): void;
}

const messageSchema = z.discriminatedUnion("kind", [
  z
    .object({ kind: z.literal("presence.projection"), projection: presenceProjectionStateSchema })
    .strict(),
  z
    .object({
      kind: z.literal("presence.request"),
      request: z.enum(["projection", "workspace.toggle"]),
    })
    .strict(),
]);

export function createPresenceChannel(): PresenceChannel {
  const projectionListeners = new Set<(value: PresenceProjectionState) => void>();
  const requestListeners = new Set<(value: PresenceRequest) => void>();
  const broadcast =
    typeof window !== "undefined" && "BroadcastChannel" in window
      ? new window.BroadcastChannel("fairy.presence.v1")
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
    requestProjection() {
      broadcast?.postMessage({ kind: "presence.request", request: "projection" });
    },
    requestWorkspaceToggle() {
      broadcast?.postMessage({
        kind: "presence.request",
        request: "workspace.toggle",
      });
    },
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
