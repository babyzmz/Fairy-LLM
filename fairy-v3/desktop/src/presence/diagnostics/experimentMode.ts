export const PRESENCE_EXPERIMENT_STORAGE_KEY = "fairy.presence.experiment";
export const PRESENCE_TARGET_FPS_STORAGE_KEY = "fairy.presence.target-fps-experiment";

export const PRESENCE_EXPERIMENT_MODES = [
  "normal",
  "static-backdrop",
  "capture-only",
  "ipc-upload-only",
  "single-renderer",
  "no-particles",
  "no-refraction",
] as const;

export type PresenceExperimentMode = typeof PRESENCE_EXPERIMENT_MODES[number];

interface ReadableStorage {
  getItem(key: string): string | null;
}

export function resolvePresenceExperimentMode(
  storage: ReadableStorage | null = browserStorage(),
  development = import.meta.env.DEV,
  diagnosticsEnabled = browserDiagnosticsEnabled(),
): PresenceExperimentMode {
  if (!development || !diagnosticsEnabled || storage === null) return "normal";
  try {
    const value = storage.getItem(PRESENCE_EXPERIMENT_STORAGE_KEY);
    return isPresenceExperimentMode(value) ? value : "normal";
  } catch {
    return "normal";
  }
}

export function backdropCommandMode(
  mode: PresenceExperimentMode,
): "normal" | "capture-only" | "ipc-upload-only" {
  if (mode === "capture-only" || mode === "ipc-upload-only") return mode;
  return "normal";
}

export function resolvePresenceTargetFpsOverride(
  storage: ReadableStorage | null = browserStorage(),
  development = import.meta.env.DEV,
  diagnosticsEnabled = browserDiagnosticsEnabled(),
): 60 | 144 | 300 | null {
  if (!development || !diagnosticsEnabled || storage === null) return null;
  try {
    const value = storage.getItem(PRESENCE_TARGET_FPS_STORAGE_KEY);
    if (value === "60") return 60;
    if (value === "144") return 144;
    if (value === "300") return 300;
  } catch {
    // Storage can be unavailable in hardened WebViews.
  }
  return null;
}

export function isPresenceExperimentMode(
  value: string | null,
): value is PresenceExperimentMode {
  return value !== null && PRESENCE_EXPERIMENT_MODES.includes(
    value as PresenceExperimentMode,
  );
}

function browserStorage(): ReadableStorage | null {
  if (typeof window === "undefined") return null;
  // Presence experiments belong to one diagnostic WebView lifetime. Persisting them in
  // localStorage silently disabled refraction on later normal development launches.
  return window.sessionStorage;
}

function browserDiagnosticsEnabled(): boolean {
  if (typeof window === "undefined") return false;
  return new URLSearchParams(window.location.search).get("presence-diagnostics") === "1";
}
