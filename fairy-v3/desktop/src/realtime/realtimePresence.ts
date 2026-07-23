export type RealtimePresenceState =
  | "idle"
  | "starting"
  | "connecting"
  | "active"
  | "listening"
  | "analyzing"
  | "speaking"
  | "stopping"
  | "completed"
  | "error";

type RealtimePresenceMessage =
  | {
      kind: "realtime.presence.request";
    }
  | {
      kind: "realtime.presence.state";
      instance_id: string;
      instance_started_at: number;
      sequence: number;
      state: RealtimePresenceState;
    };

interface BroadcastPort {
  onmessage: ((event: MessageEvent<unknown>) => void) | null;
  postMessage(message: RealtimePresenceMessage): void;
  close(): void;
}

export interface RealtimePresencePublisher {
  publish(state: RealtimePresenceState): void;
  close(): void;
}

const channelName = "fairy.realtime-presence.v1";
const validStates = new Set<RealtimePresenceState>([
  "idle",
  "starting",
  "connecting",
  "active",
  "listening",
  "analyzing",
  "speaking",
  "stopping",
  "completed",
  "error",
]);

export function createRealtimePresencePublisher(
  createPort: () => BroadcastPort | null = createBroadcastPort,
): RealtimePresencePublisher {
  const port = createPort();
  const instanceId = createInstanceId();
  const instanceStartedAt = Date.now();
  let current: RealtimePresenceState = "idle";
  let sequence = 0;

  const publishCurrent = () => {
    sequence += 1;
    port?.postMessage({
      kind: "realtime.presence.state",
      instance_id: instanceId,
      instance_started_at: instanceStartedAt,
      sequence,
      state: current,
    });
  };

  if (port !== null) {
    port.onmessage = (event) => {
      if (isRequestMessage(event.data)) publishCurrent();
    };
  }

  return {
    publish(state) {
      current = state;
      publishCurrent();
    },
    close() {
      current = "idle";
      publishCurrent();
      port?.close();
    },
  };
}

export function subscribeRealtimePresence(
  listener: (state: RealtimePresenceState) => void,
  createPort: () => BroadcastPort | null = createBroadcastPort,
): () => void {
  const port = createPort();
  if (port === null) return () => undefined;
  let latestStartedAt = -1;
  let latestInstanceId = "";
  let latestSequence = -1;

  port.onmessage = (event) => {
    const message = parseStateMessage(event.data);
    if (message === null) return;
    const sameInstance =
      message.instance_started_at === latestStartedAt
      && message.instance_id === latestInstanceId;
    if (
      message.instance_started_at < latestStartedAt
      || (sameInstance && message.sequence <= latestSequence)
    ) {
      return;
    }
    latestStartedAt = message.instance_started_at;
    latestInstanceId = message.instance_id;
    latestSequence = message.sequence;
    listener(message.state);
  };
  port.postMessage({ kind: "realtime.presence.request" });
  return () => port.close();
}

function createBroadcastPort(): BroadcastPort | null {
  if (typeof BroadcastChannel === "undefined") return null;
  return new BroadcastChannel(channelName);
}

function createInstanceId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `realtime-${Date.now().toString(36)}`;
}

function isRequestMessage(value: unknown): value is RealtimePresenceMessage {
  return (
    typeof value === "object"
    && value !== null
    && "kind" in value
    && value.kind === "realtime.presence.request"
  );
}

function parseStateMessage(
  value: unknown,
): Extract<RealtimePresenceMessage, { kind: "realtime.presence.state" }> | null {
  if (
    typeof value !== "object"
    || value === null
    || !("kind" in value)
    || value.kind !== "realtime.presence.state"
    || !("instance_id" in value)
    || typeof value.instance_id !== "string"
    || !("instance_started_at" in value)
    || typeof value.instance_started_at !== "number"
    || !Number.isFinite(value.instance_started_at)
    || !("sequence" in value)
    || typeof value.sequence !== "number"
    || !Number.isSafeInteger(value.sequence)
    || !("state" in value)
    || typeof value.state !== "string"
    || !validStates.has(value.state as RealtimePresenceState)
  ) {
    return null;
  }
  return value as Extract<
    RealtimePresenceMessage,
    { kind: "realtime.presence.state" }
  >;
}
