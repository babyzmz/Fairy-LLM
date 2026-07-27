import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createRealtimePresencePublisher,
  subscribeRealtimePresence,
  type RealtimePresenceProjection,
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
  const projection = (
    state: RealtimePresenceProjection["state"],
    sequence: number,
  ): RealtimePresenceProjection => ({
    session_id: "session-1",
    segment_id: "segment-1",
    context_epoch: 1,
    sequence,
    state,
    level: null,
    persona_digest: "a".repeat(64),
  });

  it("replays current state to a late subscriber", () => {
    const broadcast = broadcastHarness();
    const publisher = createRealtimePresencePublisher(broadcast.createPort);
    publisher.publish(projection("listening", 2));
    const observed: Array<RealtimePresenceProjection | null> = [];

    const unsubscribe = subscribeRealtimePresence(
      (state) => observed.push(state),
      broadcast.createPort,
    );

    expect(observed).toEqual([projection("listening", 2)]);
    unsubscribe();
    publisher.close();
  });

  it("ignores late state from a superseded Companion instance", () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_000);
    const broadcast = broadcastHarness();
    const oldPublisher = createRealtimePresencePublisher(broadcast.createPort);
    const observed: Array<RealtimePresenceProjection | null> = [];
    const unsubscribe = subscribeRealtimePresence(
      (state) => observed.push(state),
      broadcast.createPort,
    );
    oldPublisher.publish(projection("connecting", 1));

    vi.setSystemTime(2_000);
    const currentPublisher = createRealtimePresencePublisher(broadcast.createPort);
    currentPublisher.publish(projection("speaking", 2));
    oldPublisher.publish(projection("error", 3));
    oldPublisher.close();

    expect(observed).toEqual([
      null,
      projection("connecting", 1),
      projection("speaking", 2),
    ]);
    unsubscribe();
    currentPublisher.close();
  });
});
