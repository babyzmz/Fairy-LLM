import type { ChatUiMessage, StreamTimelineEntry } from "../../lib/stores/chatStore";

export interface ActivityEntry {
  stage: string;
  title: string;
  detail: string;
}

export interface ActivitySource {
  label: string;
  url: string;
}

export interface ActivityData {
  requestId: string;
  previewLabel: string;
  durationLabel: string;
  durationMs: number;
  entries: ActivityEntry[];
  sources: ActivitySource[];
  webAccess: {
    browseModeUsed: boolean;
    taskType: string;
    finalPageType: string;
    stopReason: string;
    finalPageUrl: string;
    selectedLinks: Array<{ text: string; url: string }>;
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function asBoolean(value: unknown): boolean {
  return value === true;
}

function activityMetaForMessage(message: ChatUiMessage | null | undefined): Record<string, unknown> {
  if (!message || message.kind !== "assistant_final") {
    return {};
  }
  const meta = asRecord(message.meta);
  const runtime = asRecord(meta.runtime);
  return asRecord(runtime.activity);
}

function webAccessMetaForMessage(message: ChatUiMessage | null | undefined): Record<string, unknown> {
  if (!message || message.kind !== "assistant_final") {
    return {};
  }
  const meta = asRecord(message.meta);
  const runtime = asRecord(meta.runtime);
  return asRecord(runtime.web_access);
}

export function timelineEntriesForRequest(entries: StreamTimelineEntry[], requestId: string): StreamTimelineEntry[] {
  return entries.filter((entry) => entry.requestId === requestId).sort((a, b) => a.timestampMs - b.timestampMs);
}

function durationFromTimeline(message: ChatUiMessage, entries: StreamTimelineEntry[]): number {
  const requestId = "requestId" in message && typeof message.requestId === "string" ? message.requestId : "";
  if (!requestId) {
    return 0;
  }
  const timeline = timelineEntriesForRequest(entries, requestId);
  const first = timeline[0]?.timestampMs ?? message.createdAt;
  const last = message.kind === "assistant_partial" ? Date.now() : timeline[timeline.length - 1]?.timestampMs ?? message.createdAt;
  return Math.max(0, last - first);
}

export function formatActivityDuration(durationMs: number): string {
  const totalSeconds = Math.max(1, Math.round(durationMs / 1000));
  if (totalSeconds < 60) {
    return `${totalSeconds}s`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

export function buildActivityPreview(message: ChatUiMessage, entries: StreamTimelineEntry[]): string {
  const requestId = "requestId" in message && typeof message.requestId === "string" ? message.requestId : "";
  if (!requestId) {
    return "";
  }
  if (message.kind === "assistant_partial") {
    return "正在思考";
  }
  if (message.kind !== "assistant_final") {
    return "";
  }
  const activity = activityMetaForMessage(message);
  const durationMs = Number(activity.duration_ms ?? durationFromTimeline(message, entries) ?? 0);
  return durationMs > 0 ? `已思考 ${formatActivityDuration(durationMs)}` : "已思考";
}

function entriesFromMeta(activity: Record<string, unknown>): ActivityEntry[] {
  if (!Array.isArray(activity.entries)) {
    return [];
  }
  return activity.entries
    .map((item) => {
      const record = asRecord(item);
      const title = asString(record.title) || asString(record.stage) || "活动";
      const detail = asString(record.detail);
      if (!title && !detail) {
        return null;
      }
      return {
        stage: asString(record.stage),
        title,
        detail,
      } satisfies ActivityEntry;
    })
    .filter((item): item is ActivityEntry => Boolean(item));
}

function entriesFromTimeline(entries: StreamTimelineEntry[]): ActivityEntry[] {
  return entries
    .filter((entry) => entry.event !== "text_delta" && entry.event !== "card" && entry.event !== "message_start" && entry.event !== "message_end")
    .slice(-12)
    .map((entry) => ({
      stage: entry.event,
      title: entry.summary || entry.event,
      detail: entry.detail || entry.summary,
    }));
}

function sourcesFromMeta(activity: Record<string, unknown>): ActivitySource[] {
  if (!Array.isArray(activity.sources)) {
    return [];
  }
  return activity.sources
    .map((item) => {
      const record = asRecord(item);
      const url = asString(record.url);
      if (!url) {
        return null;
      }
      return {
        label: asString(record.label) || url,
        url,
      } satisfies ActivitySource;
    })
    .filter((item): item is ActivitySource => Boolean(item));
}

function sourcesFromWebAccess(webAccess: Record<string, unknown>): ActivitySource[] {
  const deduped = new Map<string, ActivitySource>();
  const finalPageUrl = asString(webAccess.final_page_url);
  if (finalPageUrl) {
    deduped.set(finalPageUrl.toLowerCase(), {
      label: asString(webAccess.source_name) || asString(webAccess.source_domain) || finalPageUrl,
      url: finalPageUrl,
    });
  }
  const selectedLinks = Array.isArray(webAccess.selected_links) ? webAccess.selected_links : [];
  for (const item of selectedLinks) {
    const record = asRecord(item);
    const url = asString(record.url);
    if (!url) {
      continue;
    }
    deduped.set(url.toLowerCase(), {
      label: asString(record.text) || url,
      url,
    });
  }
  return Array.from(deduped.values()).slice(0, 12);
}

export function buildActivityData(message: ChatUiMessage | null, entries: StreamTimelineEntry[]): ActivityData | null {
  const requestId = message && "requestId" in message && typeof message.requestId === "string" ? message.requestId : "";
  if (!message || !requestId) {
    return null;
  }
  const timeline = timelineEntriesForRequest(entries, requestId);
  const activity = activityMetaForMessage(message);
  const webAccess = webAccessMetaForMessage(message);
  const durationMs = Number(activity.duration_ms ?? durationFromTimeline(message, entries) ?? 0);
  const durationLabel = formatActivityDuration(durationMs);
  const previewLabel = message.kind === "assistant_partial" ? "正在思考" : `已思考 ${durationLabel}`;
  const parsedEntries = entriesFromMeta(activity);
  const parsedSources = sourcesFromMeta(activity);
  const selectedLinks = (Array.isArray(webAccess.selected_links) ? webAccess.selected_links : [])
    .map((item) => {
      const record = asRecord(item);
      const url = asString(record.url);
      if (!url) {
        return null;
      }
      return { text: asString(record.text) || url, url };
    })
    .filter((item): item is { text: string; url: string } => Boolean(item));
  return {
    requestId,
    previewLabel,
    durationLabel,
    durationMs,
    entries: parsedEntries.length > 0 ? parsedEntries : entriesFromTimeline(timeline),
    sources: parsedSources.length > 0 ? parsedSources : sourcesFromWebAccess(webAccess),
    webAccess: {
      browseModeUsed: asBoolean(webAccess.browse_mode_used),
      taskType: asString(webAccess.task_type),
      finalPageType: asString(webAccess.final_page_type),
      stopReason: asString(webAccess.stop_reason),
      finalPageUrl: asString(webAccess.final_page_url),
      selectedLinks,
    },
  };
}

export function activityExistsForMessage(message: ChatUiMessage, entries: StreamTimelineEntry[]): boolean {
  return Boolean(buildActivityPreview(message, entries));
}
