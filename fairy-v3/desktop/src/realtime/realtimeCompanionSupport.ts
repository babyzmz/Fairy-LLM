import type {
  RealtimeBackendResolution,
  RealtimeCredentialProvider,
  RealtimeSessionStatus,
} from "../core/client";
import type { DesktopPreferences } from "../settings/client";
import type { RealtimeAssistanceProjection } from "./realtimePresence";

export function isTerminal(status: RealtimeSessionStatus): boolean {
  return ["completed", "failed", "cancelled", "interrupted"].includes(status);
}

export function assistanceTerminal(status: string): boolean {
  return ["completed", "failed", "cancelled"].includes(status);
}

export function assistanceStatusLabel(
  status: RealtimeAssistanceProjection["status"],
): string {
  switch (status) {
    case "pending":
    case "queued":
      return "Queued";
    case "running":
      return "Searching";
    case "awaiting_approval":
      return "Approval needed";
    case "paused":
      return "Core paused";
    case "completed":
      return "Ready";
    case "cancelled":
      return "Cancelled";
    case "failed":
      return "Unavailable";
  }
}

export function formatUsageMinutes(totalMs: number): string {
  const totalSeconds = Math.max(0, Math.round(totalMs / 1_000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

export function activityLabel(value: "game" | "focus"): string {
  return value === "game" ? "Game" : "Focus";
}

export function cooldownHelp(
  activity: "game" | "focus",
  intensity: "quiet" | "standard" | "active",
): string {
  if (activity === "game") {
    const seconds = intensity === "quiet" ? 90 : intensity === "standard" ? 45 : 20;
    return `Proactive Game comments wait at least ${seconds} seconds; direct replies stay immediate.`;
  }
  const minutes = intensity === "active" ? 3 : 8;
  return `Proactive Focus comments wait at least ${minutes} minutes; direct replies stay immediate.`;
}

export function standbyMessage(reason: "inactivity" | "duration_limit" | null): string {
  if (reason === "duration_limit") {
    return "Presence duration reached. Extend explicitly before waking Fairy.";
  }
  return "Fairy paused media after three quiet minutes.";
}

export function deviceId(): string {
  const key = "fairy.realtime.device-id";
  const existing = localStorage.getItem(key);
  if (existing) return existing;
  const created = crypto.randomUUID();
  localStorage.setItem(key, created);
  return created;
}

function providerLabel(provider?: DesktopPreferences["realtime_cloud_provider"]): string {
  if (provider === "gemini_live") return "Gemini Live";
  if (provider === "glm_realtime_air") return "GLM Realtime Air";
  return "GLM Realtime Flash";
}

export function backendLabel(
  backend?: RealtimeBackendResolution["backend"],
  cloudExpected = false,
  provider?: DesktopPreferences["realtime_cloud_provider"],
): string {
  if (backend === "local_mini_cpm_o45") return "Local · MiniCPM-o 4.5";
  if (backend === "cloud_live" || cloudExpected) return `Cloud Live · ${providerLabel(provider)}`;
  return "Resolving…";
}

export function resolutionGuidance(reason: string | null): string {
  switch (reason) {
    case "CLOUD_UPLOAD_CONSENT_REQUIRED":
      return "Review and enable microphone and selected-window upload for this Cloud session.";
    case "REALTIME_CREDENTIAL_MISSING":
      return "Configure the selected Cloud Live provider in Settings.";
    case "AUTO_CLOUD_FALLBACK_DISABLED":
      return "Local is not ready and Cloud fallback is disabled. Review Realtime settings.";
    case "LOCAL_VOICE_OUTPUT_INCOMPATIBLE":
      return "Local MiniCPM uses Fairy voice or text-only output. Change the voice setting.";
    case "LOCAL_MODEL_MISSING":
      return "Install and verify the Local MiniCPM model in Settings.";
    case "LOCAL_RUNTIME_MISSING":
      return "The Local Omni runtime is not installed.";
    case "LOCAL_SELF_TEST_FAILED":
    case "LOCAL_RUNTIME_QUARANTINED":
      return "Verify the Local Omni runtime in Settings before starting.";
    case "LOCAL_CUDA_UNAVAILABLE":
    case "LOCAL_DRIVER_INCOMPATIBLE":
    case "LOCAL_ADAPTER_MISMATCH":
      return "Local CUDA readiness is unavailable on the active GPU configuration.";
    case "LOCAL_VRAM_INSUFFICIENT":
    case "LOCAL_FREE_VRAM_INSUFFICIENT":
      return "Local MiniCPM does not have enough available VRAM for this activity profile.";
    default:
      return "The selected realtime backend is not ready. Review Realtime settings.";
  }
}

export function voiceOutputLabel(
  output?: DesktopPreferences["realtime_voice_output"],
): string {
  if (output === "fairy_voice") return "Fairy voice";
  if (output === "text_only") return "Text only";
  return "Provider voice";
}

export function credentialProviderFor(
  provider: DesktopPreferences["realtime_cloud_provider"],
): RealtimeCredentialProvider {
  if (provider === "gemini_live") return "gemini";
  return "zhipu";
}

export function realtimeProviderErrorMessage(code?: string | null): string {
  switch (code) {
    case "REALTIME_PROVIDER_AUTHENTICATION_FAILED":
      return "The realtime provider rejected the API key. Update it in Settings.";
    case "REALTIME_PROVIDER_QUOTA_EXHAUSTED":
      return "The realtime provider account has no available balance or quota.";
    case "REALTIME_PROVIDER_RATE_LIMITED":
      return "The realtime provider is busy or rate-limited. Try again shortly.";
    case "REALTIME_PROVIDER_TIMEOUT":
      return "The realtime provider did not respond in time.";
    case "REALTIME_PROVIDER_REQUEST_REJECTED":
    case "REALTIME_PROVIDER_PROTOCOL_ERROR":
      return "The realtime provider rejected the session configuration.";
    case "REALTIME_PROVIDER_UNAVAILABLE":
    case "REALTIME_PROVIDER_INTERRUPTED":
    case "WORKER_INTERRUPTED":
      return "The realtime provider is temporarily unavailable.";
    case "REALTIME_CREDENTIAL_MISSING":
      return "Configure the realtime provider API key in Settings before starting.";
    case "REALTIME_CLOUD_DAILY_LIMIT_REACHED":
      return "Today’s Cloud Realtime limit has been reached. Local Realtime remains available when its readiness checks pass.";
    case "REALTIME_CLOUD_USAGE_UNAVAILABLE":
      return "Fairy could not verify today’s Cloud Realtime usage, so Cloud start is blocked to protect the configured limit.";
    case "LOCAL_MODEL_MISSING":
    case "LOCAL_RUNTIME_MISSING":
    case "LOCAL_SELF_TEST_FAILED":
    case "LOCAL_RUNTIME_QUARANTINED":
    case "LOCAL_BACKEND_NOT_READY_AFTER_UNLOAD":
      return "Local Realtime is not ready to wake. Review and repair Local readiness in Settings.";
    default:
      return "Realtime session failed.";
  }
}

export function presenceLabel(value: string): string {
  if (value === "listening") return "Listening";
  if (value === "observing") return "Observing";
  if (value === "thinking") return "Thinking";
  if (value === "searching") return "Searching";
  if (value === "speaking") return "Fairy is speaking";
  if (value === "preparing") return "Preparing";
  if (value === "loading_model") return "Loading local model";
  if (value === "connecting") return "Connecting";
  if (value === "standby") return "Standing by";
  if (value === "privacy_paused") return "Privacy paused";
  if (value === "resource_limited") return "Resources limited";
  return value === "idle" ? "Idle" : "Needs attention";
}

export function messageOf(value: unknown): string {
  return value instanceof Error
    ? value.message
    : String(value || "Realtime companion unavailable");
}

export function coreErrorCode(value: unknown): string | null {
  if (typeof value !== "object" || value === null || !("errorCode" in value)) return null;
  return typeof value.errorCode === "string" ? value.errorCode : null;
}

export function mergeCaptionDelta(current: string, incoming: string): string {
  if (!incoming) return current;
  if (!current || incoming.startsWith(current)) return incoming;
  if (current.endsWith(incoming)) return current;
  return `${current}${incoming}`.slice(-4_000);
}
