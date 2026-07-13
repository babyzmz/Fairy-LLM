import { isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

import {
  presenceRuntimePolicySchema,
  type PresenceRuntimePolicy,
} from "../domain/runtimePolicy";

export const PRESENCE_RUNTIME_POLICY_EVENT = "presence-runtime-policy";
export const PRESENCE_RUNTIME_POLICY_CHANNEL = "fairy.presence.runtime-policy.v1";

export interface PresenceRuntimePolicySource {
  subscribe(listener: (policy: PresenceRuntimePolicy) => void): Promise<() => void>;
}

export function createPresenceRuntimePolicySource(): PresenceRuntimePolicySource {
  if (!isTauri()) {
    return {
      async subscribe(listener) {
        if (typeof BroadcastChannel === "undefined") return () => undefined;
        const channel = new BroadcastChannel(PRESENCE_RUNTIME_POLICY_CHANNEL);
        channel.addEventListener("message", (event: MessageEvent<unknown>) => {
          const message = event.data;
          if (
            typeof message !== "object" ||
            message === null ||
            !("kind" in message) ||
            message.kind !== "presence.runtime-policy" ||
            !("policy" in message)
          ) {
            return;
          }
          const parsed = presenceRuntimePolicySchema.safeParse(message.policy);
          if (parsed.success) listener(parsed.data);
        });
        return () => channel.close();
      },
    };
  }
  return {
    async subscribe(listener) {
      return listen<unknown>(PRESENCE_RUNTIME_POLICY_EVENT, (event) => {
        const parsed = presenceRuntimePolicySchema.safeParse(event.payload);
        if (parsed.success) listener(parsed.data);
      });
    },
  };
}
