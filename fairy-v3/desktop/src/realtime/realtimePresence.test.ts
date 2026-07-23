import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createRealtimePresencePublisher,
  subscribeRealtimePresence,
  type RealtimePresenceState,
} from "./realtimePresence";

interface TestPort {
  onmessage: ((event: MessageEvent<unknown>) => void) | null;
  postMessage(message: unknown): void;
  close(): void;
}

function broadcastHarness() {
  const ports = new Set<TestPort>();
  const createPort = () => {
    const port: TestPort = {
      onmessage: null,
      postMessage(message) {
        for (const candidate of ports) {
          if (candidate !== port) {
            candidate.onmessage?.({ data: message } as MessageEvent<unknown>);
          }
        }
      },
      close() {
        ports.delete(port);
      },
    };
    ports.add(port);
    return port;
  };
  return { createPort };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("realtime presence channel", () => {
  it("replays current state to a late subscriber", () => {
    const broadcast = broadcastHarness();
    const publisher = createRealtimePresencePublisher(broadcast.createPort);
    publisher.publish("listening");
    const observed: RealtimePresenceState[] = [];

    const unsubscribe = subscribeRealtimePresence(
      (state) => observed.push(state),
      broadcast.createPort,
    );

    expect(observed).toEqual(["listening"]);
    unsubscribe();
    publisher.close();
  });

  it("ignores late state from a superseded Companion instance", () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_000);
    const broadcast = broadcastHarness();
    const oldPublisher = createRealtimePresencePublisher(broadcast.createPort);
    const observed: RealtimePresenceState[] = [];
    const unsubscribe = subscribeRealtimePresence(
      (state) => observed.push(state),
      broadcast.createPort,
    );
    oldPublisher.publish("connecting");

    vi.setSystemTime(2_000);
    const currentPublisher = createRealtimePresencePublisher(broadcast.createPort);
    currentPublisher.publish("speaking");
    oldPublisher.publish("error");
    oldPublisher.close();

    expect(observed).toEqual(["idle", "connecting", "speaking"]);
    unsubscribe();
    currentPublisher.close();
  });
});
