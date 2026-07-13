import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createPresenceVoiceLevelSource,
  publishPresenceVoiceLevel,
} from "./voiceLevelEvents";

const originalBroadcastChannel = globalThis.BroadcastChannel;

afterEach(() => {
  vi.restoreAllMocks();
  globalThis.BroadcastChannel = originalBroadcastChannel;
});

describe("presence voice levels", () => {
  it("clamps and transfers transient levels without a durable event", () => {
    const channels = new Map<string, Set<FakeBroadcastChannel>>();
    class FakeBroadcastChannel {
      private readonly listeners = new Set<(event: MessageEvent<unknown>) => void>();

      constructor(private readonly name: string) {
        const peers = channels.get(name) ?? new Set();
        peers.add(this);
        channels.set(name, peers);
      }

      postMessage(message: unknown) {
        for (const peer of channels.get(this.name) ?? []) {
          if (peer === this) continue;
          for (const listener of peer.listeners) {
            listener(new MessageEvent("message", { data: message }));
          }
        }
      }

      addEventListener(_type: string, listener: EventListenerOrEventListenerObject) {
        if (typeof listener === "function") {
          this.listeners.add(listener as (event: MessageEvent<unknown>) => void);
        }
      }

      removeEventListener(_type: string, listener: EventListenerOrEventListenerObject) {
        if (typeof listener === "function") {
          this.listeners.delete(listener as (event: MessageEvent<unknown>) => void);
        }
      }

      close() {
        channels.get(this.name)?.delete(this);
        this.listeners.clear();
      }
    }
    globalThis.BroadcastChannel = FakeBroadcastChannel as unknown as typeof BroadcastChannel;
    const listener = vi.fn();
    const stop = createPresenceVoiceLevelSource().subscribe(listener);

    publishPresenceVoiceLevel(1.8);
    expect(listener).toHaveBeenCalledOnce();
    expect(listener.mock.calls[0][0]).toMatchObject({ level: 1 });
    stop();
  });
});
