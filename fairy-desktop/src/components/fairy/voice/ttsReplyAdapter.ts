import type { VoicePlaybackItem } from "./voiceTypes";
import type { ChatReplyVoicePayload, VoicePolicyDecision } from "./voiceTypes";
import { decideSpeechPolicy } from "./voicePolicy";
import { renderSpeechContent } from "./speechContentAdapter";

export interface ChatReplyVoiceAdapterResult {
  decision: VoicePolicyDecision;
  playbackItem: VoicePlaybackItem | null;
}

export function adaptChatReplyToPlayback(payload: ChatReplyVoicePayload): ChatReplyVoiceAdapterResult {
  const decision = decideSpeechPolicy(payload);
  const rendered = renderSpeechContent({
    text: payload.text,
    cards: payload.cards,
    speech: payload.speech,
    mode: decision.mode,
  });

  if (decision.mode === "silent" || !rendered.text.trim()) {
    return { decision, playbackItem: null };
  }

  return {
    decision,
    playbackItem: {
      id: `chat-tts-${payload.requestId}`,
      eventId: `chat-reply-${payload.requestId}`,
      eventType: "chat_reply_ready",
      sourceType: "chat_tts",
      priority: decision.priority,
      kind: "tts",
      key: `chat_tts:${payload.requestId}`,
      preview: rendered.preview,
      interruptCurrent: true,
      speechMode: rendered.mode,
      text: rendered.text,
      systemVoice: false,
      dedupeKey: `chat_tts:${payload.requestId}`,
    },
  };
}
