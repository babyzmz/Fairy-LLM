import { afterEach, describe, expect, it, vi } from "vitest";

import { createPresenceRuntimePolicySource } from "./runtimePolicyEvents";

const originalBroadcastChannel = globalThis.BroadcastChannel;

afterEach(() => {
  globalThis.BroadcastChannel = originalBroadcastChannel;
  vi.restoreAllMocks();
});

describe("presence runtime policy source", () => {
  it("accepts only a bounded frame-rate policy", async () => {
    const messages: Array<(event: MessageEvent<unknown>) => void> = [];
    class FakeBroadcastChannel {
      addEventListener(_type: string, listener: EventListenerOrEventListenerObject) {
        messages.push(listener as (event: MessageEvent<unknown>) => void);
      }
      close() {}
    }
    globalThis.BroadcastChannel = FakeBroadcastChannel as unknown as typeof BroadcastChannel;
    const listener = vi.fn();
    const stop = await createPresenceRuntimePolicySource().subscribe(listener);
    messages[0]?.(new MessageEvent("message", {
      data: {
        kind: "presence.runtime-policy",
        policy: {
          schema_version: 1,
          frame_rate_limit: 15,
          power_saver: true,
          foreground_fullscreen: false,
        },
      },
    }));
    messages[0]?.(new MessageEvent("message", {
      data: {
        kind: "presence.runtime-policy",
        policy: {
          schema_version: 1,
          frame_rate_limit: 1,
          power_saver: true,
          foreground_fullscreen: false,
          process_name: "forbidden.exe",
        },
      },
    }));
    expect(listener).toHaveBeenCalledOnce();
    expect(listener).toHaveBeenCalledWith(expect.objectContaining({ frame_rate_limit: 15 }));
    stop();
  });
});
