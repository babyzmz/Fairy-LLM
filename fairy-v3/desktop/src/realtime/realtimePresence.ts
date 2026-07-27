export type RealtimePresenceState =
  | "idle"
  | "preparing"
  | "loading_model"
  | "connecting"
  | "listening"
  | "observing"
  | "thinking"
  | "searching"
  | "speaking"
  | "standby"
  | "privacy_paused"
  | "resource_limited"
  | "error";

export interface RealtimePresenceProjection {
  session_id: string;
  segment_id: string;
  context_epoch: number;
  sequence: number;
  state: RealtimePresenceState;
  level: number | null;
  persona_digest: string;
  requested_activity_profile: "auto" | "game" | "focus";
  effective_activity: "game" | "focus";
  interaction_intensity: "quiet" | "standard" | "active";
  backend: "local_mini_cpm_o45" | "cloud_live";
  cloud_provider: "gemini_live" | "glm_realtime_flash" | "glm_realtime_air" | null;
  standby_reason: "inactivity" | "duration_limit" | null;
  wake_available: boolean;
  duration_extension_required: boolean;
}

export type RealtimeAssistanceProjectionStatus =
  | "pending"
  | "queued"
  | "running"
  | "awaiting_approval"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";

export interface RealtimeAssistanceProjection {
  session_id: string;
  segment_id: string;
  context_epoch: number;
  request_id: string;
  public_intent: string;
  status: RealtimeAssistanceProjectionStatus;
  error_code: string | null;
  public_summary: string | null;
}

export type RealtimeAssistanceStateUpdate = Omit<
  RealtimeAssistanceProjection,
  "public_intent" | "public_summary"
> & {
  public_intent?: string;
  public_summary?: string | null;
};

type RealtimePresenceMessage =
  | {
      kind: "realtime.presence.request";
    }
  | {
      kind: "realtime.presence.projection";
      instance_id: string;
      instance_started_at: number;
      sequence: number;
      projection: RealtimePresenceProjection | null;
    };

interface BroadcastPort {
  onmessage: ((event: MessageEvent<unknown>) => void) | null;
  postMessage(message: RealtimePresenceMessage): void;
  close(): void;
}

export interface RealtimePresencePublisher {
  publish(projection: RealtimePresenceProjection | null): void;
  close(): void;
}

const channelName = "fairy.realtime-presence.v2";
const validStates = new Set<RealtimePresenceState>([
  "idle",
  "preparing",
  "loading_model",
  "connecting",
  "listening",
  "observing",
  "thinking",
  "searching",
  "speaking",
  "standby",
  "privacy_paused",
  "resource_limited",
  "error",
]);
const validProfiles = new Set(["auto", "game", "focus"]);
const validEffectiveActivities = new Set(["game", "focus"]);
const validIntensities = new Set(["quiet", "standard", "active"]);
const validBackends = new Set(["local_mini_cpm_o45", "cloud_live"]);
const validCloudProviders = new Set([
  "gemini_live",
  "glm_realtime_flash",
  "glm_realtime_air",
]);
const validStandbyReasons = new Set(["inactivity", "duration_limit"]);
const validAssistanceStatuses = new Set<RealtimeAssistanceProjectionStatus>([
  "pending",
  "queued",
  "running",
  "awaiting_approval",
  "paused",
  "completed",
  "failed",
  "cancelled",
]);

export function createRealtimePresencePublisher(
  createPort: () => BroadcastPort | null = createBroadcastPort,
): RealtimePresencePublisher {
  const port = createPort();
  const instanceId = createInstanceId();
  const instanceStartedAt = Date.now();
  let current: RealtimePresenceProjection | null = null;
  let sequence = 0;

  const publishCurrent = () => {
    sequence += 1;
    port?.postMessage({
      kind: "realtime.presence.projection",
      instance_id: instanceId,
      instance_started_at: instanceStartedAt,
      sequence,
      projection: current,
    });
  };

  if (port !== null) {
    port.onmessage = (event) => {
      if (isRequestMessage(event.data)) publishCurrent();
    };
  }

  return {
    publish(projection) {
      if (projection !== null && !isPresenceProjection(projection)) return;
      current = projection;
      publishCurrent();
    },
    close() {
      current = null;
      publishCurrent();
      port?.close();
    },
  };
}

export function subscribeRealtimePresence(
  listener: (projection: RealtimePresenceProjection | null) => void,
  createPort: () => BroadcastPort | null = createBroadcastPort,
): () => void {
  const port = createPort();
  if (port === null) return () => undefined;
  let latestStartedAt = -1;
  let latestInstanceId = "";
  let latestSequence = -1;

  port.onmessage = (event) => {
    const message = parseProjectionMessage(event.data);
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
    listener(message.projection);
  };
  port.postMessage({ kind: "realtime.presence.request" });
  return () => port.close();
}

export function isPresenceProjection(
  value: unknown,
): value is RealtimePresenceProjection {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<RealtimePresenceProjection>;
  return (
    typeof candidate.session_id === "string"
    && candidate.session_id.length > 0
    && typeof candidate.segment_id === "string"
    && candidate.segment_id.length > 0
    && Number.isSafeInteger(candidate.context_epoch)
    && (candidate.context_epoch ?? 0) > 0
    && Number.isSafeInteger(candidate.sequence)
    && (candidate.sequence ?? 0) > 0
    && typeof candidate.state === "string"
    && validStates.has(candidate.state as RealtimePresenceState)
    && (
      candidate.level === null
      || (
        Number.isSafeInteger(candidate.level)
        && (candidate.level ?? -1) >= 0
        && (candidate.level ?? 256) <= 255
      )
    )
    && typeof candidate.persona_digest === "string"
    && /^[0-9a-f]{64}$/.test(candidate.persona_digest)
    && typeof candidate.requested_activity_profile === "string"
    && validProfiles.has(candidate.requested_activity_profile)
    && typeof candidate.effective_activity === "string"
    && validEffectiveActivities.has(candidate.effective_activity)
    && typeof candidate.interaction_intensity === "string"
    && validIntensities.has(candidate.interaction_intensity)
    && typeof candidate.backend === "string"
    && validBackends.has(candidate.backend)
    && (
      candidate.cloud_provider === null
      || (
        typeof candidate.cloud_provider === "string"
        && validCloudProviders.has(candidate.cloud_provider)
      )
    )
    && (
      candidate.standby_reason === null
      || (
        typeof candidate.standby_reason === "string"
        && validStandbyReasons.has(candidate.standby_reason)
      )
    )
    && typeof candidate.wake_available === "boolean"
    && typeof candidate.duration_extension_required === "boolean"
  );
}

export function isRealtimeAssistanceProjection(
  value: unknown,
): value is RealtimeAssistanceProjection {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<RealtimeAssistanceProjection>;
  return (
    boundedText(candidate.session_id, 128)
    && boundedText(candidate.segment_id, 128)
    && Number.isSafeInteger(candidate.context_epoch)
    && (candidate.context_epoch ?? 0) > 0
    && boundedText(candidate.request_id, 128)
    && boundedText(candidate.public_intent, 4_000)
    && typeof candidate.status === "string"
    && validAssistanceStatuses.has(candidate.status as RealtimeAssistanceProjectionStatus)
    && (
      candidate.error_code === null
      || (
        typeof candidate.error_code === "string"
        && /^[A-Z][A-Z0-9_]{0,127}$/.test(candidate.error_code)
      )
    )
    && (
      candidate.public_summary === null
      || boundedText(candidate.public_summary, 2_000)
    )
  );
}

export function isRealtimeAssistanceStateUpdate(
  value: unknown,
): value is RealtimeAssistanceStateUpdate {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<RealtimeAssistanceStateUpdate>;
  return (
    boundedText(candidate.session_id, 128)
    && boundedText(candidate.segment_id, 128)
    && Number.isSafeInteger(candidate.context_epoch)
    && (candidate.context_epoch ?? 0) > 0
    && boundedText(candidate.request_id, 128)
    && typeof candidate.status === "string"
    && validAssistanceStatuses.has(candidate.status as RealtimeAssistanceProjectionStatus)
    && (
      candidate.error_code === null
      || (
        typeof candidate.error_code === "string"
        && /^[A-Z][A-Z0-9_]{0,127}$/.test(candidate.error_code)
      )
    )
    && (
      candidate.public_intent === undefined
      || boundedText(candidate.public_intent, 4_000)
    )
    && (
      candidate.public_summary === undefined
      || candidate.public_summary === null
      || boundedText(candidate.public_summary, 2_000)
    )
  );
}

export function applyRealtimeAssistanceStateUpdate(
  current: RealtimeAssistanceProjection[],
  update: RealtimeAssistanceStateUpdate,
): RealtimeAssistanceProjection[] {
  const previous = current.find(
    (item) =>
      item.session_id === update.session_id
      && item.request_id === update.request_id,
  );
  if (previous === undefined && !isRealtimeAssistanceProjection(update)) {
    return current;
  }
  return mergeRealtimeAssistanceProjection(current, {
    ...(previous ?? update as RealtimeAssistanceProjection),
    ...update,
    public_intent: update.public_intent ?? previous?.public_intent ?? "",
    public_summary: update.public_summary ?? previous?.public_summary ?? null,
  });
}

export function mergeRealtimeAssistanceProjection(
  current: RealtimeAssistanceProjection[],
  next: RealtimeAssistanceProjection,
): RealtimeAssistanceProjection[] {
  const sameSession = current.filter((item) => item.session_id === next.session_id);
  const previous = sameSession.find((item) => item.request_id === next.request_id);
  const merged = {
    ...next,
    public_intent: next.public_intent || previous?.public_intent || "",
    public_summary: next.public_summary ?? previous?.public_summary ?? null,
  };
  return [
    ...sameSession.filter((item) => item.request_id !== next.request_id),
    merged,
  ].slice(-6);
}

function boundedText(value: unknown, maxChars: number): value is string {
  return (
    typeof value === "string"
    && value.trim().length > 0
    && [...value].length <= maxChars
  );
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

function parseProjectionMessage(
  value: unknown,
): Extract<
  RealtimePresenceMessage,
  { kind: "realtime.presence.projection" }
> | null {
  if (
    typeof value !== "object"
    || value === null
    || !("kind" in value)
    || value.kind !== "realtime.presence.projection"
    || !("instance_id" in value)
    || typeof value.instance_id !== "string"
    || !("instance_started_at" in value)
    || typeof value.instance_started_at !== "number"
    || !Number.isFinite(value.instance_started_at)
    || !("sequence" in value)
    || typeof value.sequence !== "number"
    || !Number.isSafeInteger(value.sequence)
    || !("projection" in value)
    || (value.projection !== null && !isPresenceProjection(value.projection))
  ) {
    return null;
  }
  return value as Extract<
    RealtimePresenceMessage,
    { kind: "realtime.presence.projection" }
  >;
}
