import { isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

import {
  presenceInteractionSnapshotSchema,
  type PresenceInteractionSnapshot,
} from "../domain/interaction";

export const PRESENCE_INTERACTION_EVENT = "presence-interaction-snapshot";

export interface PresenceInteractionSource {
  subscribe(
    listener: (snapshot: PresenceInteractionSnapshot) => void,
  ): Promise<() => void>;
}

export function createPresenceInteractionSource(): PresenceInteractionSource {
  if (!isTauri()) {
    return { subscribe: async () => () => undefined };
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
