import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createRealtimePresencePublisher,
  applyRealtimeAssistanceStateUpdate,
  isRealtimeAssistanceProjection,
  mergeRealtimeAssistanceProjection,
  subscribeRealtimePresence,
  type RealtimeAssistanceProjection,
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
    requested_activity_profile: "auto",
    effective_activity: "focus",
    interaction_intensity: "standard",
    backend: "cloud_live",
    cloud_provider: "glm_realtime_flash",
    standby_reason: state === "standby" ? "inactivity" : null,
    wake_available: state === "standby",
    duration_extension_required: false,
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

describe("realtime assistance projection", () => {
  const projection: RealtimeAssistanceProjection = {
    session_id: "session-1",
    segment_id: "segment-1",
    context_epoch: 1,
    request_id: "request-1",
    public_intent: "Find the east gate",
    status: "running",
    error_code: null,
    public_summary: null,
  };

  it("accepts only bounded public fields", () => {
    expect(isRealtimeAssistanceProjection(projection)).toBe(true);
    expect(isRealtimeAssistanceProjection({
      ...projection,
      public_summary: "x".repeat(2_001),
    })).toBe(false);
    expect(isRealtimeAssistanceProjection({
      ...projection,
      error_code: "unsafe detail",
    })).toBe(false);
  });

  it("retains the safe summary when a later worker acknowledgement omits it", () => {
    const complete = { ...projection, status: "completed" as const, public_summary: "Ready." };
    expect(mergeRealtimeAssistanceProjection(
      [complete],
      { ...complete, public_summary: null },
    )).toEqual([complete]);
    expect(mergeRealtimeAssistanceProjection(
      [complete],
      { ...projection, session_id: "session-2", request_id: "request-2" },
    )).toHaveLength(1);
  });

  it("applies a partial Worker acknowledgement only to an existing request", () => {
    const current = { ...projection, status: "completed" as const, public_summary: "Ready." };
    expect(applyRealtimeAssistanceStateUpdate([current], {
      session_id: current.session_id,
      segment_id: current.segment_id,
      context_epoch: current.context_epoch,
      request_id: current.request_id,
      status: "failed",
      error_code: "ASSISTANCE_RESULT_DELIVERY_FAILED",
    })[0]).toMatchObject({
      status: "failed",
      public_intent: current.public_intent,
      public_summary: "Ready.",
    });
    expect(applyRealtimeAssistanceStateUpdate([], {
      session_id: current.session_id,
      segment_id: current.segment_id,
      context_epoch: current.context_epoch,
      request_id: "unknown",
      status: "failed",
      error_code: "ASSISTANCE_RESULT_DELIVERY_FAILED",
    })).toEqual([]);
  });
});
