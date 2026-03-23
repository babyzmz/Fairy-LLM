import { API_BASE_URL } from "../config/env";
import type { DesktopSystemState, SystemActionResponse, SystemStateResponse } from "../types/api";
import { apiRequest } from "./client";

const DESKTOP_ACTIONS = new Set(["restart_backend", "reveal_asset_folder", "open_panel", "focus_window", "show_notification"]);

async function invokeTauri<T>(command: string, args?: Record<string, unknown>): Promise<T | null> {
  try {
    const mod = await import("@tauri-apps/api/core");
    return await mod.invoke<T>(command, args);
  } catch {
    return null;
  }
}

function wrapRuntimeState(runtimeState: SystemStateResponse): DesktopSystemState {
  return {
    bridge_status: runtimeState.backend_status,
    backend_url: API_BASE_URL,
    bridge_message: runtimeState.last_error || "",
    pid: null,
    bridge_events: [],
    runtime_state: runtimeState,
  };
}

export async function getSystemState(): Promise<DesktopSystemState> {
  const tauriState = await invokeTauri<DesktopSystemState>("system_state");
  if (tauriState) {
    return tauriState;
  }
  const runtimeState = await apiRequest<SystemStateResponse>("/system/state");
  return wrapRuntimeState(runtimeState);
}

export async function performSystemAction(
  action: string,
  payload: Record<string, unknown> = {},
): Promise<SystemActionResponse> {
  const tauriCommand = DESKTOP_ACTIONS.has(action) ? "system_action_execute" : "backend_control";
  const tauriArgs =
    tauriCommand === "system_action_execute"
      ? { action, payload }
      : { action, request_id: typeof payload.request_id === "string" ? payload.request_id : undefined };
  const tauriResponse = await invokeTauri<SystemActionResponse>(tauriCommand, tauriArgs);
  if (tauriResponse) {
    return tauriResponse;
  }
  const runtimeResponse = await apiRequest<Omit<SystemActionResponse, "system_state">>("/system/actions", {
    method: "POST",
    body: JSON.stringify({ action, payload }),
  });
  return {
    ...runtimeResponse,
    system_state: await getSystemState(),
  };
}
