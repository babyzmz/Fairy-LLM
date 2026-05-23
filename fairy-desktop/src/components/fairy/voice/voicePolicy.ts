import type { CardUnion, SpeechMeta } from "../../../lib/types/api";
import type { ChatReplyVoicePayload, VoicePolicyDecision } from "./voiceTypes";

function compact(text: string): string {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function looksLikeCode(text: string): boolean {
  const value = String(text || "");
  if (value.includes("```")) {
    return true;
  }
  const codeSignals = [/^\s*(const|let|var|function|class|if|for|while|return)\b/m, /[{}();=<>\[\]]/, /<\/?[a-z][^>]*>/i];
  return codeSignals.some((pattern) => pattern.test(value));
}

function looksLikeLog(text: string): boolean {
  const value = String(text || "");
  const logSignals = [/\b(INFO|WARN|WARNING|ERROR|DEBUG|TRACE)\b/, /\d{2}:\d{2}:\d{2}/, /\d{4}-\d{2}-\d{2}/];
  return logSignals.some((pattern) => pattern.test(value));
}

function looksLikeTable(text: string): boolean {
  const lines = String(text || "").split(/\r?\n/).filter(Boolean);
  const pipeLines = lines.filter((line) => (line.match(/\|/g) || []).length >= 2).length;
  return pipeLines >= 2;
}

function isLongStructuredContent(text: string, cards: CardUnion[]): boolean {
  const normalized = compact(text);
  const lineBreaks = (String(text || "").match(/\n/g) || []).length;
  const listLike = /(^[-*•]\s)|(^\d+\.\s)/m.test(text);
  return normalized.length > 420 || cards.length > 1 || lineBreaks >= 5 || listLike;
}

function hasNewsShape(cards: CardUnion[], speech: SpeechMeta | undefined): boolean {
  return cards.some((card) => card.type === "news_list") || String(speech?.mode || "") === "summary_first";
}

export interface VoicePolicyInput extends ChatReplyVoicePayload {}

export function decideSpeechPolicy(input: VoicePolicyInput): VoicePolicyDecision {
  const text = compact(input.text);
  const speechModeHint = String(input.speech?.mode || "").trim();
  const summaryModeHints = new Set(["summary_first", "concise_structured"]);
  const fullModeHints = new Set(["detailed_explainer"]);

  if (!text && input.cards.length === 0) {
    return { mode: "silent", reason: "empty_reply", priority: 1 };
  }

  if (speechModeHint === "silent") {
    return { mode: "silent", reason: "backend_silent_hint", priority: 1 };
  }

  if (looksLikeCode(input.text) || looksLikeLog(input.text) || looksLikeTable(input.text)) {
    return { mode: "minimal_read", reason: "code_log_or_table", priority: 4 };
  }

  if (summaryModeHints.has(speechModeHint) || hasNewsShape(input.cards, input.speech) || isLongStructuredContent(input.text, input.cards)) {
    return { mode: "summary_read", reason: speechModeHint || "long_or_structured_reply", priority: 4 };
  }

  if (fullModeHints.has(speechModeHint)) {
    return { mode: "full_read", reason: speechModeHint, priority: 4 };
  }

  return { mode: "full_read", reason: speechModeHint || "default_full_read", priority: 4 };
}
