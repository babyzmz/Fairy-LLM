import type { CardUnion, NewsCardEnvelope, SpeechMeta } from "../../../lib/types/api";
import type { SpeechMode, SpeechRenderResult } from "./voiceTypes";

function compact(text: string): string {
  return String(text || "").replace(/\s+/g, " ").trim();
}

export function stripSpeechNoise(text: string): string {
  let value = String(text || "");
  value = value.replace(/```[\s\S]*?```/g, " ");
  value = value.replace(/`([^`]+)`/g, "$1");
  value = value.replace(/!\[[^\]]*]\([^)]*\)/g, " ");
  value = value.replace(/\[([^\]]+)]\(([^)]+)\)/g, "$1");
  value = value.replace(/https?:\/\/\S+/gi, " ");
  value = value.replace(/^#{1,6}\s+/gm, "");
  value = value.replace(/^[-*•]\s+/gm, "");
  value = value.replace(/^\d+\.\s+/gm, "");
  value = value.replace(/\|/g, " ");
  return compact(value);
}

function firstSentences(text: string, maxSentences: number, maxChars: number): string {
  const normalized = stripSpeechNoise(text);
  if (!normalized) {
    return "";
  }
  const sentences = normalized.split(/(?<=[.!?。！？])\s+/).filter(Boolean);
  const joined = (sentences.slice(0, maxSentences).join(" ") || normalized).trim();
  if (joined.length <= maxChars) {
    return joined;
  }
  return `${joined.slice(0, Math.max(0, maxChars - 3)).trim()}...`;
}

function renderNewsSummary(cards: CardUnion[]): string {
  const newsCard = cards.find((card): card is NewsCardEnvelope => card.type === "news_list");
  if (!newsCard) {
    return "";
  }
  const lines = newsCard.data.items
    .slice(0, 3)
    .map((item) => stripSpeechNoise(item.headline || item.title || item.summary || item.snippet || ""))
    .filter(Boolean);
  return firstSentences(lines.join(". "), 3, 220);
}

function renderMinimalNotice(text: string): string {
  const source = String(text || "");
  if (source.includes("```") || /(^\s*(const|let|var|function|class)\b)/m.test(source)) {
    return "I have the code ready on screen. I will not read it line by line.";
  }
  if (/\b(INFO|WARN|WARNING|ERROR|DEBUG|TRACE)\b/.test(source) || /\d{2}:\d{2}:\d{2}/.test(source)) {
    return "The log output is on screen. I will keep the spoken summary minimal.";
  }
  return "The detailed content is on screen. I will keep the spoken summary minimal.";
}

export interface SpeechContentAdapterInput {
  text: string;
  cards: CardUnion[];
  speech?: SpeechMeta;
  mode: SpeechMode;
}

export function renderSpeechContent(input: SpeechContentAdapterInput): SpeechRenderResult {
  const backendSpeechText = stripSpeechNoise(String(input.speech?.text || ""));
  const cleanedText = stripSpeechNoise(input.text);

  if (input.mode === "silent") {
    return { mode: "silent", text: "", preview: "", reason: "silent_mode" };
  }

  if (input.mode === "minimal_read") {
    const text = renderMinimalNotice(input.text);
    return {
      mode: "minimal_read",
      text,
      preview: text,
      reason: "minimal_notice",
    };
  }

  if (input.mode === "summary_read") {
    const newsSummary = renderNewsSummary(input.cards);
    const text = backendSpeechText || newsSummary || firstSentences(cleanedText, 3, 220);
    return {
      mode: "summary_read",
      text,
      preview: text.slice(0, 160),
      reason: backendSpeechText ? "backend_summary" : newsSummary ? "news_summary" : "local_summary",
    };
  }

  const fullRead = cleanedText || backendSpeechText;
  const safeText = fullRead.length > 1200 ? `${fullRead.slice(0, 1197).trim()}...` : fullRead;
  return {
    mode: "full_read",
    text: safeText,
    preview: safeText.slice(0, 160),
    reason: "full_read_cleaned_text",
  };
}
