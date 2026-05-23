import { API_BASE_URL } from "../config/env";
import type {
  DesktopSystemState,
  PersistedAttachment,
  SystemActionResponse,
  SystemStateResponse,
  VoiceSynthesizeResponse,
} from "../types/api";
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

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const chunk = bytes.subarray(offset, offset + chunkSize);
    binary += String.fromCharCode(...chunk);
  }
  return btoa(binary);
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

export async function persistChatAttachments(files: File[]): Promise<PersistedAttachment[]> {
  if (files.length === 0) {
    return [];
  }
  const mod = await import("@tauri-apps/api/core").catch(() => null);
  if (!mod) {
    throw new Error("Attachments require the Fairy desktop shell.");
  }
  return Promise.all(
    files.map(async (file) => {
      const dataBase64 = arrayBufferToBase64(await file.arrayBuffer());
      return mod.invoke<PersistedAttachment>("persist_chat_attachment", {
        file_name: file.name,
        mime_type: file.type || undefined,
        data_base64: dataBase64,
      });
    }),
  );
}

export async function synthesizeVoiceAudio(text: string, systemVoice = false): Promise<VoiceSynthesizeResponse> {
  return apiRequest<VoiceSynthesizeResponse>("/system/voice/synthesize", {
    method: "POST",
    body: JSON.stringify({ text, system_voice: systemVoice }),
  });
}
