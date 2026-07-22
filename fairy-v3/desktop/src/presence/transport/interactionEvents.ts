import { invoke, isTauri } from "@tauri-apps/api/core";
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
      let latestSequence = -1;
      const deliver = (value: unknown) => {
        const parsed = presenceInteractionSnapshotSchema.safeParse(value);
        if (!parsed.success || parsed.data.sequence <= latestSequence) return;
        latestSequence = parsed.data.sequence;
        listener(parsed.data);
      };
      const unlisten = await listen<unknown>(PRESENCE_INTERACTION_EVENT, (event) => {
        deliver(event.payload);
      });
      try {
        deliver(await invoke<unknown>("pet_interaction_snapshot_get"));
      } catch {
        // The live event remains authoritative when startup replay is unavailable.
      }
      return unlisten;
    },
  };
}
