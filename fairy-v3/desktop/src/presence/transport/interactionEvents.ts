import { isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

import {
  presenceInteractionSnapshotSchema,
  type PresenceInteractionSnapshot,
} from "../domain/interaction";

export const PRESENCE_INTERACTION_EVENT = "presence-interaction-snapshot";
export const PRESENCE_INTERACTION_CHANNEL = "fairy.presence.interaction.v1";

export interface PresenceInteractionSource {
  subscribe(
    listener: (snapshot: PresenceInteractionSnapshot) => void,
  ): Promise<() => void>;
}

export function createPresenceInteractionSource(): PresenceInteractionSource {
  if (!isTauri()) {
    return {
      async subscribe(listener) {
        if (typeof BroadcastChannel === "undefined") return () => undefined;
        const channel = new BroadcastChannel(PRESENCE_INTERACTION_CHANNEL);
        channel.addEventListener("message", (event: MessageEvent<unknown>) => {
          const message = event.data;
          if (
            typeof message !== "object" ||
            message === null ||
            !("kind" in message) ||
            message.kind !== "presence.interaction" ||
            !("snapshot" in message)
          ) {
            return;
          }
          const parsed = presenceInteractionSnapshotSchema.safeParse(message.snapshot);
          if (parsed.success) listener(parsed.data);
        });
        return () => channel.close();
      },
    };
  }
  return {
    async subscribe(listener) {
      return listen<unknown>(PRESENCE_INTERACTION_EVENT, (event) => {
        const parsed = presenceInteractionSnapshotSchema.safeParse(event.payload);
        if (parsed.success) listener(parsed.data);
      });
    },
  };
}
