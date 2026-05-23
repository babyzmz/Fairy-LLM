import type { RuntimeAssistantState } from "../../../lib/types/api";
import type { ChatReplyVoicePayload, VoiceEvent, VoiceTimelineEntryLike } from "./voiceTypes";

function newEventId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}

export function createLifecycleVoiceEvent(
  event: "starting" | "ready" | "reused" | "timeout" | "stopped" | "spawn-failed",
): VoiceEvent {
  const typeMap = {
    starting: "startup_booting",
    ready: "startup_ready",
    reused: "startup_reused",
    timeout: "startup_timeout",
    stopped: "startup_stopped",
    "spawn-failed": "startup_failed",
  } as const;
  return {
    id: newEventId(`lifecycle-${event}`),
    type: typeMap[event],
    sourceType: "startup_voice",
    timestampMs: Date.now(),
  };
}

export function createRuntimeStateVoiceEvent(
  currentState: RuntimeAssistantState,
  previousState: RuntimeAssistantState | null,
): VoiceEvent {
  return {
    id: newEventId(`runtime-${currentState}`),
    type: "runtime_state",
    sourceType: "runtime_voice",
    timestampMs: Date.now(),
    currentState,
    previousState,
  };
}

export function createTimelineVoiceEvent(entry: VoiceTimelineEntryLike): VoiceEvent {
  return {
    id: entry.id || newEventId("timeline"),
    type: "runtime_timeline",
    sourceType: "runtime_voice",
    timestampMs: entry.timestampMs || Date.now(),
    entry,
  };
}

export function createChatReplyReadyVoiceEvent(payload: ChatReplyVoicePayload): VoiceEvent {
  return {
    id: `chat-reply-${payload.requestId || newEventId("reply")}`,
    type: "chat_reply_ready",
    sourceType: "chat_tts",
    timestampMs: Date.now(),
    payload,
  };
}

export function createChatReplyChunkVoiceEvent(requestId: string, text: string): VoiceEvent {
  return {
    id: newEventId(`chat-chunk-${requestId || "pending"}`),
    type: "chat_reply_chunk",
    sourceType: "chat_tts",
    timestampMs: Date.now(),
    requestId,
    text,
  };
}

export function createChatReplyCancelledVoiceEvent(requestId?: string, reason?: string): VoiceEvent {
  return {
    id: `chat-cancelled-${requestId || newEventId("cancelled")}`,
    type: "chat_reply_cancelled",
    sourceType: "chat_tts",
    timestampMs: Date.now(),
    requestId,
    reason,
  };
}

export function createChatReplyErrorVoiceEvent(requestId?: string, message?: string): VoiceEvent {
  return {
    id: `chat-error-${requestId || newEventId("error")}`,
    type: "chat_reply_error",
    sourceType: "chat_tts",
    timestampMs: Date.now(),
    requestId,
    message,
  };
}
