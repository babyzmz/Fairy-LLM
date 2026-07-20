import type {
  AssistantTurn,
  EventEnvelope,
  TraceStep,
  TraceStepKind,
  TraceStepStatus,
  TurnTrace,
} from "../core/client";
import { publicActivities } from "./legacyActivityProjection";
import type { TurnTraceQueryState } from "./useTurnTraces";

export { publicActivities } from "./legacyActivityProjection";
export type { PublicActivity } from "./legacyActivityProjection";

export type WorkChainTone = "active" | "tool" | "success" | "error" | "muted";

export interface WorkChainStep {
  id: string;
  sequence: number;
  kind: TraceStepKind;
  status: TraceStepStatus;
  summary: string;
  detail: string | null;
  parentStepId: string | null;
  causedByStepId: string | null;
  modelRole: TraceStep["model_role"];
  modelId: string | null;
  providerAttemptId: string | null;
  commandRunId: string | null;
  commandName: string | null;
  artifactRefs: string[];
  createdAt: string;
  updatedAt: string;
  startedAt: string | null;
  completedAt: string | null;
  durationMs: number | null;
  visibility: TraceStep["visibility"];
  source: "trace" | "event" | "legacy" | "voice";
}

export interface WorkChainProjection {
  steps: WorkChainStep[];
  current: WorkChainStep;
  terminal: boolean;
  completedSteps: number;
  totalSteps: number;
  toolCount: number;
  modelCount: number;
  durationMs: number | null;
}

interface ProjectWorkChainInput {
  trace?: TurnTrace | null;
  traceState?: TurnTraceQueryState | null;
  turn?: AssistantTurn | null;
  turnId?: string | null;
  events?: EventEnvelope[];
  developerMode?: boolean;
  now?: number;
}

const TRACE_EVENT_PREFIX = "turn.trace.step.";
const TERMINAL_STATUSES = new Set<TraceStepStatus>([
  "succeeded",
  "failed",
  "cancelled",
  "skipped",
]);
const ACTIVE_STATUSES = new Set<TraceStepStatus>(["pending", "running", "waiting"]);
const ACTIVE_MODEL_SUMMARIES = new Set([
  "Analyzing the request",
  "Working on the request",
  "Reviewing the response",
]);
const STEP_KINDS = new Set<TraceStepKind>([
  "route",
  "plan",
  "reasoning",
  "model",
  "tool",
  "approval",
  "observation",
  "verification",
  "artifact",
  "response",
  "voice",
]);
const STEP_STATUSES = new Set<TraceStepStatus>([
  "pending",
  "running",
  "waiting",
  "succeeded",
  "failed",
  "cancelled",
  "skipped",
]);

export function projectWorkChain({
  trace = null,
  traceState = null,
  turn = null,
  turnId = null,
  events = [],
  developerMode = false,
  now = Date.now(),
}: ProjectWorkChainInput): WorkChainProjection {
  const resolvedTurnId = trace?.turn_id ?? turn?.id ?? turnId;
  const boundCommandRuns = new Set(
    (trace?.steps ?? [])
      .map((step) => step.command_run_id)
      .filter((runId): runId is string => runId !== null),
  );
  const relevantEvents = resolvedTurnId === null
    ? []
    : events
        .filter(
          (event) =>
            event.payload.turn_id === resolvedTurnId ||
            (event.run_id !== null && boundCommandRuns.has(event.run_id)),
        )
        .sort((left, right) => left.cursor - right.cursor);
  const commandNames = commandNamesByRun(relevantEvents);
  const steps = new Map<string, WorkChainStep>();

  for (const step of trace?.steps ?? []) {
    if (!isVisible(step.visibility, developerMode)) continue;
    const projected = fromTraceStep(step);
    projected.commandName =
      projected.commandRunId === null ? null : (commandNames.get(projected.commandRunId) ?? null);
    steps.set(projected.id, projected);
  }

  for (const event of relevantEvents) {
    const projected = fromTraceEvent(event, resolvedTurnId, developerMode);
    if (projected === null) continue;
    const existing = steps.get(projected.id);
    if (existing !== undefined && timestamp(event.created_at) < timestamp(existing.updatedAt)) {
      continue;
    }
    const commandRunId = existing?.commandRunId ?? projected.commandRunId;
    steps.set(projected.id, {
      ...existing,
      ...projected,
      modelId: existing?.modelId ?? projected.modelId,
      providerAttemptId: existing?.providerAttemptId ?? projected.providerAttemptId,
      commandRunId,
      commandName: commandRunId === null ? null : (commandNames.get(commandRunId) ?? null),
      createdAt: existing?.createdAt ?? projected.createdAt,
      startedAt: projected.startedAt ?? existing?.startedAt ?? null,
      completedAt: projected.completedAt ?? existing?.completedAt ?? null,
      durationMs: projected.durationMs ?? existing?.durationMs ?? null,
      source: existing?.source ?? projected.source,
    });
  }

  let ordered = [...steps.values()].sort(
    (left, right) => left.sequence - right.sequence || left.id.localeCompare(right.id),
  );
  if (ordered.length === 0) {
    ordered = legacySteps(turn, resolvedTurnId, events);
  }
  if (ordered.length === 0) {
    ordered = [fallbackStep(turn, trace, traceState, resolvedTurnId)];
  }

  const terminalStatus = resolvedTerminalStatus(turn, trace, traceState, ordered);
  const staleActiveStepIds = new Set(
    ordered.filter((step) => ACTIVE_STATUSES.has(step.status)).map((step) => step.id),
  );
  if (terminalStatus !== null) {
    ordered = ordered.map((step) =>
      ACTIVE_STATUSES.has(step.status)
        ? {
            ...step,
            status: terminalStatus,
            completedAt: turn?.completed_at ?? trace?.completed_at ?? step.completedAt,
          }
        : step,
    );
  }
  const active = ordered.filter((step) => ACTIVE_STATUSES.has(step.status));
  const projectedCurrent =
    active.at(-1) ?? ordered.at(-1) ?? fallbackStep(turn, trace, traceState, resolvedTurnId);
  const current = terminalStatus !== null && (
    staleActiveStepIds.has(projectedCurrent.id) ||
    ACTIVE_MODEL_SUMMARIES.has(projectedCurrent.summary)
  )
    ? {
        ...projectedCurrent,
        summary: terminalSummary(turn, terminalStatus),
      }
    : projectedCurrent;
  const terminal =
    terminalStatus !== null ||
    (turn === null && trace === null && traceState === null && active.length === 0);

  return {
    steps: ordered,
    current,
    terminal,
    completedSteps: ordered.filter((step) => TERMINAL_STATUSES.has(step.status)).length,
    totalSteps: ordered.length,
    toolCount: ordered.filter((step) => step.kind === "tool").length,
    modelCount: new Set(
      ordered
        .filter((step) => step.kind === "model")
        .map((step) => step.modelId ?? step.modelRole ?? step.id),
    ).size,
    durationMs: traceDuration(trace, ordered, terminal, now),
  };
}

function terminalSummary(
  turn: AssistantTurn | null,
  status: TraceStepStatus,
): string {
  if (turn?.status === "failed" || status === "failed") return "Response failed";
  if (turn?.status === "cancelled" || status === "cancelled") return "Response stopped";
  return "Response ready";
}

export function withVoiceStep(
  projection: WorkChainProjection,
  turnId: string,
  state: "idle" | "preparing" | "speaking" | "failed",
  now = new Date().toISOString(),
): WorkChainProjection {
  if (state === "idle") return projection;
  const status: TraceStepStatus = state === "failed" ? "failed" : "running";
  const summary = state === "preparing"
    ? "Preparing voice"
    : state === "speaking"
      ? "Speaking reply"
      : "Voice playback failed";
  const voice: WorkChainStep = {
    id: `voice:${turnId}:${state}`,
    sequence: Math.max(0, ...projection.steps.map((step) => step.sequence)) + 1,
    kind: "voice",
    status,
    summary,
    detail: null,
    parentStepId: null,
    causedByStepId: projection.steps.findLast((step) => step.kind === "response")?.id ?? null,
    modelRole: null,
    modelId: null,
    providerAttemptId: null,
    commandRunId: null,
    commandName: null,
    artifactRefs: [],
    createdAt: now,
    updatedAt: now,
    startedAt: now,
    completedAt: state === "failed" ? now : null,
    durationMs: null,
    visibility: "user",
    source: "voice",
  };
  const steps = [...projection.steps, voice];
  return {
    ...projection,
    steps,
    current: voice,
    terminal: state === "failed" ? projection.terminal : false,
    completedSteps: projection.completedSteps + (state === "failed" ? 1 : 0),
    totalSteps: projection.totalSteps + 1,
  };
}

export function workChainTone(step: WorkChainStep): WorkChainTone {
  if (step.status === "failed") return "error";
  if (step.status === "cancelled" || step.status === "skipped") return "muted";
  if (step.status === "succeeded") return "success";
  if (step.kind === "tool" || step.kind === "approval") return "tool";
  return "active";
}

export function modelRoleLabel(step: WorkChainStep): string | null {
  if (step.kind !== "model") return null;
  const known: Record<string, string> = {
    "deepseek/deepseek-v4-pro": "DeepSeek coordination",
    "z-ai/glm-5.2": "GLM review",
    "moonshotai/kimi-k2.7-code": "Kimi implementation",
    "google/gemini-3.1-flash-lite-image": "Gemini image generation",
    "google/lyria-3-pro-preview": "Lyria music generation",
    "bytedance/seedance-2.0": "Seedance video generation",
    "nvidia/nemotron-3-ultra-550b-a55b:free": "Nemotron response",
    "qwen/qwen3-coder:free": "Qwen code generation",
  };
  if (step.modelId !== null && known[step.modelId] !== undefined) return known[step.modelId];
  if (step.modelRole === "coordinator") return "Model coordination";
  if (step.modelRole === "reviewer") return "Model review";
  return "Model execution";
}

function fromTraceStep(step: TraceStep): WorkChainStep {
  return {
    id: step.id,
    sequence: step.sequence,
    kind: step.kind,
    status: step.status,
    summary: safeText(step.public_summary, 240) ?? defaultSummary(step.kind),
    detail: safeText(step.public_detail, 600),
    parentStepId: step.parent_step_id,
    causedByStepId: step.caused_by_step_id,
    modelRole: step.model_role,
    modelId: safeText(step.model_id, 255),
    providerAttemptId: step.provider_attempt_id,
    commandRunId: step.command_run_id,
    commandName: null,
    artifactRefs: step.artifact_refs.slice(0, 12),
    createdAt: step.created_at,
    updatedAt: step.updated_at,
    startedAt: step.started_at,
    completedAt: step.completed_at,
    durationMs: safeDuration(step.duration_ms),
    visibility: step.visibility,
    source: "trace",
  };
}

function fromTraceEvent(
  event: EventEnvelope,
  turnId: string | null,
  developerMode: boolean,
): WorkChainStep | null {
  if (!event.event_type.startsWith(TRACE_EVENT_PREFIX)) return null;
  if (!isVisible(event.visibility, developerMode)) return null;
  if (turnId !== null && event.payload.turn_id !== turnId) return null;
  const id = safeText(event.payload.trace_step_id, 64);
  const sequence = safeInteger(event.payload.sequence);
  const kind = safeKind(event.payload.kind);
  const status = safeStatus(event.payload.status);
  const summary = safeText(event.payload.public_summary, 240);
  if (id === null || sequence === null || kind === null || status === null || summary === null) {
    return null;
  }
  return {
    id,
    sequence,
    kind,
    status,
    summary,
    detail: safeText(event.payload.public_detail, 600),
    parentStepId: safeText(event.payload.parent_step_id, 64),
    causedByStepId: safeText(event.payload.caused_by_step_id, 64),
    modelRole: safeModelRole(event.payload.model_role),
    modelId: null,
    providerAttemptId: null,
    commandRunId: event.run_id,
    commandName: null,
    artifactRefs: safeStringList(event.payload.artifact_refs),
    createdAt: event.created_at,
    updatedAt: event.created_at,
    startedAt: status === "running" ? event.created_at : null,
    completedAt: TERMINAL_STATUSES.has(status) ? event.created_at : null,
    durationMs: safeDuration(event.payload.duration_ms),
    visibility: event.visibility === "developer" ? "developer" : "user",
    source: "event",
  };
}

function legacySteps(
  turn: AssistantTurn | null,
  turnId: string | null,
  events: EventEnvelope[],
): WorkChainStep[] {
  if (turn === null) return [];
  return publicActivities(turn, events).map((activity, index) => ({
    id: `legacy:${turnId ?? turn.id}:${activity.id}`,
    sequence: index + 1,
    kind: activity.tone === "tool" ? "tool" : activity.label.includes("response") ? "response" : "reasoning",
    status: activity.tone === "error"
      ? "failed"
      : activity.tone === "success" || activity.tone === "muted"
        ? "succeeded"
        : "running",
    summary: activity.label,
    detail: null,
    parentStepId: null,
    causedByStepId: null,
    modelRole: null,
    modelId: null,
    providerAttemptId: null,
    commandRunId: null,
    commandName: null,
    artifactRefs: [],
    createdAt: activity.createdAt,
    updatedAt: activity.createdAt,
    startedAt: activity.createdAt,
    completedAt: activity.tone === "success" || activity.tone === "error" ? activity.createdAt : null,
    durationMs: null,
    visibility: "user",
    source: "legacy",
  }));
}

function fallbackStep(
  turn: AssistantTurn | null,
  trace: TurnTrace | null,
  traceState: TurnTraceQueryState | null,
  turnId: string | null,
): WorkChainStep {
  const status = fallbackStatus(turn, trace, traceState);
  return {
    id: `fallback:${turnId ?? trace?.id ?? "unknown"}`,
    sequence: 1,
    kind: "reasoning",
    status,
    summary: fallbackLabel(turn, trace, traceState),
    detail:
      traceState?.status === "error"
        ? traceState.error
        : trace?.legacy
          ? "This Turn predates the durable work-chain format."
          : null,
    parentStepId: null,
    causedByStepId: null,
    modelRole: null,
    modelId: null,
    providerAttemptId: null,
    commandRunId: null,
    commandName: null,
    artifactRefs: [],
    createdAt: trace?.created_at ?? turn?.created_at ?? new Date(0).toISOString(),
    updatedAt: trace?.updated_at ?? turn?.updated_at ?? new Date(0).toISOString(),
    startedAt: trace?.started_at ?? turn?.started_at ?? null,
    completedAt: trace?.completed_at ?? turn?.completed_at ?? null,
    durationMs: null,
    visibility: "user",
    source: "legacy",
  };
}

function fallbackStatus(
  turn: AssistantTurn | null,
  trace: TurnTrace | null,
  traceState: TurnTraceQueryState | null,
): TraceStepStatus {
  if (turn?.status === "completed") return "succeeded";
  if (turn?.status === "failed") return "failed";
  if (turn?.status === "cancelled") return "cancelled";
  if (turn?.status === "waiting_for_tool") return "waiting";
  if (traceState?.status === "error") return "failed";
  if (traceState?.status === "loading") return "running";
  if (trace !== null && (trace.legacy || trace.completed_at !== null)) return "succeeded";
  return turn === null ? "succeeded" : "running";
}

function fallbackLabel(
  turn: AssistantTurn | null,
  trace: TurnTrace | null,
  traceState: TurnTraceQueryState | null,
): string {
  if (turn?.status === "completed") return "Response ready";
  if (turn?.status === "failed") return "Response failed";
  if (turn?.status === "cancelled") return "Response stopped";
  if (turn?.status === "waiting_for_tool") return "Waiting for approval";
  if (turn !== null) return "Preparing response";
  if (traceState?.status === "error") return "Work chain unavailable";
  if (traceState?.status === "loading") return "Loading work chain";
  if (traceState?.status === "loaded" && trace === null) return "No work chain recorded";
  return trace?.legacy ? "Previous activity" : "Waiting for durable activity";
}

function resolvedTerminalStatus(
  turn: AssistantTurn | null,
  trace: TurnTrace | null,
  traceState: TurnTraceQueryState | null,
  steps: WorkChainStep[],
): TraceStepStatus | null {
  if (turn?.status === "completed") return "succeeded";
  if (turn?.status === "failed") return "failed";
  if (turn?.status === "cancelled") return "cancelled";
  if (traceState?.status === "error") return "failed";
  if (traceState?.status === "loaded" && trace === null && turn === null) return "succeeded";
  if (trace !== null && (trace.legacy || trace.completed_at !== null)) {
    if (steps.some((step) => step.status === "failed")) return "failed";
    if (steps.some((step) => step.status === "cancelled")) return "cancelled";
    return "succeeded";
  }
  return null;
}

function commandNamesByRun(events: EventEnvelope[]): Map<string, string> {
  const names = new Map<string, string>();
  for (const event of events) {
    const name = safeText(event.payload.command_name, 128);
    if (event.run_id !== null && name !== null) names.set(event.run_id, name);
  }
  return names;
}

function defaultSummary(kind: TraceStepKind): string {
  const values: Record<TraceStepKind, string> = {
    route: "Selecting the right model",
    plan: "Planning the work",
    reasoning: "Evaluating the next step",
    model: "Running model",
    tool: "Using a tool",
    approval: "Waiting for approval",
    observation: "Reviewing tool result",
    verification: "Verifying the result",
    artifact: "Preparing output",
    response: "Writing response",
    voice: "Preparing voice",
  };
  return values[kind];
}

function traceDuration(
  trace: TurnTrace | null,
  steps: WorkChainStep[],
  terminal: boolean,
  now: number,
): number | null {
  const startedAt = trace?.started_at ?? steps.find((step) => step.startedAt !== null)?.startedAt ?? steps.at(0)?.createdAt;
  if (startedAt === null || startedAt === undefined) return null;
  const start = timestamp(startedAt);
  if (start === 0) return null;
  const latestCompleted = [...steps]
    .reverse()
    .find((step) => step.completedAt !== null)?.completedAt;
  const endValue = trace?.completed_at ?? (terminal ? latestCompleted : null);
  const end = endValue === null || endValue === undefined ? now : timestamp(endValue);
  return Math.max(0, end - start);
}

function isVisible(visibility: TraceStep["visibility"] | EventEnvelope["visibility"], developerMode: boolean): boolean {
  return visibility === "user" || (developerMode && visibility === "developer");
}

function safeText(value: unknown, maxLength: number): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (trimmed.length === 0) return null;
  return trimmed.slice(0, maxLength);
}

function safeInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0 ? value : null;
}

function safeDuration(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function safeKind(value: unknown): TraceStepKind | null {
  return typeof value === "string" && STEP_KINDS.has(value as TraceStepKind)
    ? value as TraceStepKind
    : null;
}

function safeStatus(value: unknown): TraceStepStatus | null {
  return typeof value === "string" && STEP_STATUSES.has(value as TraceStepStatus)
    ? value as TraceStepStatus
    : null;
}

function safeModelRole(value: unknown): TraceStep["model_role"] {
  return value === "coordinator" || value === "primary" || value === "reviewer" ? value : null;
}

function safeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => safeText(item, 64))
    .filter((item): item is string => item !== null)
    .slice(0, 12);
}

function timestamp(value: string): number {
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}
