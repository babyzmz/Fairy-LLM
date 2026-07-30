import type {
  RealtimeRetryableMediaChannel,
  RealtimeWorkerStatus,
} from "../core/client";
import type {
  RealtimeAssistanceProjection,
  RealtimePresenceProjection,
} from "./realtimePresence";

export interface CaptureSurface {
  kind: "display" | "window";
  source_id: string;
  label: string;
  width: number;
  height: number;
}

export type RealtimeStartupStage =
  | "resolving_backend"
  | "creating_session"
  | "preparing_fairy_voice"
  | "loading_persona"
  | "starting_backend_runtime"
  | "acquiring_microphone"
  | "acquiring_observed_window"
  | "acquiring_application_audio"
  | "active";

export type WorkerEvent =
  | {
      type: "startup_stage";
      session_id: string;
      segment_id?: string;
      context_epoch?: number;
      stage: RealtimeStartupStage;
    }
  | { type: "session_state"; session_id: string; segment_id: string; context_epoch: number; status: string; backend: "local_mini_cpm_o45" | "cloud_live"; cloud_provider?: string | null; error_code?: string | null }
  | { type: "public_caption"; session_id: string; segment_id: string; context_epoch: number; sequence: number; text: string; stable: boolean; speaker: "user" | "assistant"; speech_output?: "fairy_voice" | "provider_native_voice" | "text_only"; speech_generation?: number; persona_digest?: string }
  | ({ type: "presence_projection" } & RealtimePresenceProjection)
  | { type: "barge_in"; session_id: string; segment_id: string; context_epoch: number; speech_generation: number }
  | { type: "tool_request"; session_id: string; segment_id: string; context_epoch: number; call_id: string; tool_name: string; public_intent: string }
  | ({ type: "assistance_state" } & Partial<RealtimeAssistanceProjection> & {
      session_id: string;
      segment_id: string;
      context_epoch: number;
      request_id: string;
      status: RealtimeAssistanceProjection["status"];
      error_code: string | null;
    })
  | { type: "usage"; session_id: string; segment_id: string; context_epoch: number; audio_input_ms: number; audio_output_ms: number; video_frame_count: number; interruption_count: number; tool_call_count: number }
  | { type: "media_channel_state"; session_id: string; segment_id: string; context_epoch: number; channel: RealtimeWorkerStatus["media_channels"][number]["channel"]; sequence: number; status: RealtimeWorkerStatus["media_channels"][number]["status"]; error_code: string | null }
  | { type: "capture_scope_state"; session_id: string; segment_id: string; context_epoch: number; mode: "selected_window" | "follow_foreground"; source_sequence: number; source_available: boolean; privacy_paused: boolean; sensitive_category: "secure_desktop" | "fairy_owned" | "credential_application" | "financial_or_private" | "protected_content" | "user_excluded" | null; error_code: string | null }
  | { type: "capture_source_changed"; session_id: string; segment_id: string; context_epoch: number; source_id: number; source_sequence: number; status: string; error_code: string | null }
  | { type: "local_backend_unloaded"; session_id: string; segment_id: string; context_epoch: number }
  | { type: "sidecar_recovery"; status: "restarting" | "quarantined"; segment_id: string | null; restart_used: boolean; quarantined: boolean; context_interrupted: boolean; failure_count: number; error_code: string | null }
  | { type: "worker_interrupted"; error_code: string }
  | { type: "ready" | "pong" };

export interface RealtimeUsage {
  audio_input_ms: number;
  audio_output_ms: number;
  video_frame_count: number;
  interruption_count: number;
  tool_call_count: number;
}

export interface RealtimeWorkerIdentity {
  sessionId: string;
  segmentId: string;
  contextEpoch: number;
}

export const EMPTY_USAGE: RealtimeUsage = {
  audio_input_ms: 0,
  audio_output_ms: 0,
  video_frame_count: 0,
  interruption_count: 0,
  tool_call_count: 0,
};

export const EMPTY_SIDECAR: RealtimeWorkerStatus["sidecar"] = {
  restart_used: false,
  quarantined: false,
  context_interrupted: false,
  failure_count: 0,
  error_code: null,
};

export function mediaChannelLabel(
  channel: RealtimeWorkerStatus["media_channels"][number]["channel"],
): string {
  switch (channel) {
    case "microphone":
      return "Microphone";
    case "selected_window":
      return "Observed window";
    case "selected_application_audio":
      return "Application audio";
    case "fairy_render_reference":
      return "Echo reference";
    case "voice_output":
      return "Voice output";
  }
}

export function isRetryableMediaChannel(
  channel: RealtimeWorkerStatus["media_channels"][number]["channel"],
): channel is RealtimeRetryableMediaChannel {
  return channel !== "voice_output";
}
