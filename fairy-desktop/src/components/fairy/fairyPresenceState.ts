import type { BackendStatus, StreamTimelineEntry } from "../../lib/stores/chatStore";
import type { DesktopSystemState, FairyMeta, RuntimeAssistantState } from "../../lib/types/api";
import type { CardUnion } from "../../lib/types/api";
import type { FairyAvatarMode, FairyAvatarSignal } from "./FairyAvatar";
import { resolveFairyAvatarSignal } from "./FairyAvatar";

export interface FairyPresenceStateInput {
  backendStatus: BackendStatus;
  systemState: DesktopSystemState | null;
  isSending: boolean;
  isStreaming: boolean;
  partialAssistantText: string;
  streamedCards: CardUnion[];
  streamError: string;
  lastError: string;
  desktopNotification: string;
  systemPanelOpen: boolean;
  streamTimeline: StreamTimelineEntry[];
}

export interface FairyPresenceState {
  mode: FairyAvatarMode;
  signal: FairyAvatarSignal;
  label: string;
  detail: string;
}

const THINKING_STAGES = new Set([
  "resolution_start",
  "rule_preclassified",
  "contract_loaded",
  "candidate_generated",
  "semantic_consistency_checked",
  "candidate_rejected",
  "capability_arbitrated",
  "arbitration_correction_applied",
  "arbitration_complete",
  "slot_extracted",
  "slot_normalized",
  "slot_validated",
  "slot_default_applied",
  "carryover_applied",
  "carryover_rejected",
  "missing_required_slot",
  "clarification_required",
  "orchestration_plan_built",
]);

const ANALYZING_STAGES = new Set([
  "orchestration_step_start",
  "orchestration_step_complete",
  "orchestration_partial_failure",
  "orchestration_step_retry",
  "orchestration_step_skipped",
  "desktop_action_dispatch",
  "desktop_action_result",
  "Desktop Automation",
]);

const FAIRY_STATES = new Set(["standby", "relaxed", "thinking", "focused", "uncertain", "alert"]);

function normalizeNumber(value: unknown): number | undefined {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return undefined;
  }
  return Math.max(0, Math.min(1, value));
}

function signalFromFairyMeta(meta: FairyMeta | undefined): FairyAvatarSignal | null {
  if (!meta?.state || !FAIRY_STATES.has(meta.state)) {
    return null;
  }
  return {
    state: meta.state,
    certainty: normalizeNumber(meta.certainty),
    urgency: normalizeNumber(meta.urgency),
  };
}

function latestTimelineEntry(entries: StreamTimelineEntry[]): StreamTimelineEntry | null {
  if (!entries.length) {
    return null;
  }
  return entries[entries.length - 1] ?? null;
}

function describeTimeline(entry: StreamTimelineEntry | null): string {
  if (!entry) {
    return "";
  }
  return entry.summary || entry.event || "";
}

function mapBackendState(
  state: RuntimeAssistantState,
  detail: string,
  signal: FairyAvatarSignal = resolveFairyAvatarSignal(state),
): FairyPresenceState {
  const labels: Record<RuntimeAssistantState, string> = {
    booting: "Booting",
    warming_up: "Warming Up",
    idle: "Idle",
    thinking: "Thinking",
    analyzing: "Analyzing",
    replying: "Replying",
    error: "Error",
    sleeping: "Sleeping",
  };
  return {
    mode: state,
    signal,
    label: labels[state],
    detail,
  };
}

function presence(
  mode: FairyAvatarMode,
  label: string,
  detail: string,
  signal: FairyAvatarSignal = resolveFairyAvatarSignal(mode),
): FairyPresenceState {
  return {
    mode,
    signal,
    label,
    detail,
  };
}

export function deriveFairyPresenceState(input: FairyPresenceStateInput): FairyPresenceState {
  const {
    backendStatus,
    systemState,
    isSending,
    isStreaming,
    partialAssistantText,
    streamedCards,
    streamError,
    lastError,
    desktopNotification,
    systemPanelOpen,
    streamTimeline,
  } = input;

  const bridgeStatus = String(systemState?.bridge_status || "");
  const runtimeStatus = String(systemState?.runtime_state?.backend_status || "");
  const runtimeCurrentState = systemState?.runtime_state?.current_state;
  const runtimeLastError = String(systemState?.runtime_state?.last_error || "");
  const runtimeTrace = systemState?.runtime_state?.runtime_state_trace ?? [];
  const latest = latestTimelineEntry(streamTimeline);
  const latestSummary = describeTimeline(latest);
  const effectiveError = streamError || runtimeLastError || (backendStatus === "online" ? lastError : "");
  const runtimeDetail =
    String(runtimeTrace[runtimeTrace.length - 1]?.reason || "").trim() ||
    (runtimeCurrentState === "idle" ? "Ready for the next request." : latestSummary || "");
  const runtimeFairySignal = signalFromFairyMeta(systemState?.runtime_state?.fairy);

  if (runtimeCurrentState) {
    return mapBackendState(
      runtimeCurrentState,
      runtimeCurrentState === "error" ? effectiveError || runtimeDetail : runtimeDetail,
      runtimeFairySignal ?? resolveFairyAvatarSignal(runtimeCurrentState),
    );
  }

  if (bridgeStatus === "starting" || backendStatus === "unknown") {
    return presence("booting", "Booting", "Connecting Fairy shell to the local runtime.");
  }

  if (backendStatus === "offline") {
    return presence(
      effectiveError ? "error" : "sleeping",
      effectiveError ? "Offline Error" : "Sleeping",
      effectiveError || "Runtime is currently offline.",
    );
  }

  if (bridgeStatus && bridgeStatus !== "ready" && bridgeStatus !== "reused" && !isStreaming) {
    return presence("warming_up", "Warming Up", `Bridge status: ${bridgeStatus}`);
  }

  if (effectiveError) {
    return presence("error", "Error", effectiveError);
  }

  if (isStreaming) {
    if (partialAssistantText.trim() || streamedCards.length > 0 || latest?.event === "text_delta" || latest?.event === "card") {
      return presence("replying", "Replying", latestSummary || "Streaming a response.");
    }
    if (latestSummary && (THINKING_STAGES.has(latestSummary) || latestSummary.startsWith("resolution"))) {
      return presence("thinking", "Thinking", latestSummary);
    }
    const mode = ANALYZING_STAGES.has(latestSummary) || latestSummary.startsWith("progress:") ? "analyzing" : "thinking";
    return presence(
      mode,
      mode === "analyzing" ? "Analyzing" : "Thinking",
      latestSummary || "Working through the current request.",
    );
  }

  if (isSending) {
    return presence("thinking", "Thinking", "Preparing the next request.");
  }

  if (desktopNotification) {
    return presence("replying", "Notifying", desktopNotification);
  }

  if (systemPanelOpen) {
    return presence("analyzing", "System Panel", "Inspecting runtime and bridge status.");
  }

  const ready = runtimeStatus === "ready" || bridgeStatus === "ready" || bridgeStatus === "reused";
  return presence(
    ready ? "idle" : "warming_up",
    ready ? "Idle" : "Warming Up",
    ready ? "Ready for the next request." : "Waiting for the desktop runtime to settle.",
  );
}
