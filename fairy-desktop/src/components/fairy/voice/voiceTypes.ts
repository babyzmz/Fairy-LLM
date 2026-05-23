import type { CardUnion, RuntimeAssistantState, SpeechMeta } from "../../../lib/types/api";

export type VoiceSourceType = "startup_voice" | "runtime_voice" | "chat_tts";
export type SpeechMode = "full_read" | "summary_read" | "minimal_read" | "silent";
export type VoicePriority = 1 | 2 | 3 | 4 | 5;
export type VoicePlaybackKind = "asset" | "tts";

export interface ChatReplyVoicePayload {
  requestId: string;
  text: string;
  cards: CardUnion[];
  speech?: SpeechMeta;
  meta?: Record<string, unknown>;
}

export interface VoiceTimelineEntryLike {
  id?: string;
  requestId?: string;
  sessionId?: string;
  event?: string;
  sequence?: number;
  timestampMs?: number;
  summary?: string;
  detail?: string;
}

export type VoiceEvent =
  | {
      id: string;
      type: "startup_booting" | "startup_ready" | "startup_reused" | "startup_timeout" | "startup_stopped" | "startup_failed";
      sourceType: "startup_voice";
      timestampMs: number;
    }
  | {
      id: string;
      type: "runtime_state";
      sourceType: "runtime_voice";
      timestampMs: number;
      currentState: RuntimeAssistantState;
      previousState: RuntimeAssistantState | null;
    }
  | {
      id: string;
      type: "runtime_timeline";
      sourceType: "runtime_voice";
      timestampMs: number;
      entry: VoiceTimelineEntryLike;
    }
  | {
      id: string;
      type: "chat_reply_ready";
      sourceType: "chat_tts";
      timestampMs: number;
      payload: ChatReplyVoicePayload;
    }
  | {
      id: string;
      type: "chat_reply_chunk";
      sourceType: "chat_tts";
      timestampMs: number;
      requestId: string;
      text: string;
    }
  | {
      id: string;
      type: "chat_reply_cancelled";
      sourceType: "chat_tts";
      timestampMs: number;
      requestId?: string;
      reason?: string;
    }
  | {
      id: string;
      type: "chat_reply_error";
      sourceType: "chat_tts";
      timestampMs: number;
      requestId?: string;
      message?: string;
    };

export interface VoicePolicyDecision {
  mode: SpeechMode;
  reason: string;
  priority: VoicePriority;
}

export interface SpeechRenderResult {
  mode: SpeechMode;
  text: string;
  preview: string;
  reason: string;
}

export interface VoicePlaybackItem {
  id: string;
  eventId: string;
  eventType: VoiceEvent["type"];
  sourceType: VoiceSourceType;
  priority: VoicePriority;
  kind: VoicePlaybackKind;
  key: string;
  preview: string;
  interruptCurrent: boolean;
  speechMode?: SpeechMode;
  assetSrc?: string;
  text?: string;
  systemVoice?: boolean;
  dedupeKey: string;
}

export interface VoicePlaybackState {
  current: VoicePlaybackItem | null;
  queue: VoicePlaybackItem[];
}

export interface VoicePlaybackDiagnostic {
  stage: string;
  detail: string;
  error: string;
  itemKey: string;
  preview: string;
  timestampMs: number;
}

export interface FairyVoiceRuntimeDebug {
  currentRuntimeState: string;
  lastVoiceEvent: string;
  lastVoiceKey: string;
  currentSpeakingSource: string;
  queueLength: number;
  cooldowns: Array<{ key: string; remainingMs: number }>;
  lastSpeechMode: SpeechMode | "";
  lastRenderedSpokenTextPreview: string;
  lastRuntimeTransition: string;
  lastPlaybackStage: string;
  lastPlaybackDetail: string;
  lastPlaybackError: string;
  lastPlaybackItemKey: string;
  lastPlaybackTimestampMs: number;
}
