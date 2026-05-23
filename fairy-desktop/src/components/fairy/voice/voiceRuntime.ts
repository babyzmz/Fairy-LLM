import {
  type FairyRuntimeVoiceKey,
  getRuntimeVoiceEntry,
  type RuntimeVoiceEntry,
  voiceForRuntimeStateChange,
  voiceForTimelineEntry,
  voicesForLifecycleEvent,
} from "./runtimeVoiceRegistry";
import { adaptChatReplyToPlayback } from "./ttsReplyAdapter";
import type {
  VoicePlaybackDiagnostic,
  FairyVoiceRuntimeDebug,
  VoiceEvent,
  VoicePlaybackItem,
  VoicePlaybackState,
  VoicePriority,
} from "./voiceTypes";
import { VoicePlaybackController } from "./voicePlaybackController";

function shouldInterruptCurrent(current: VoicePlaybackItem | null, incoming: VoicePlaybackItem): boolean {
  if (!current) {
    return false;
  }
  if (incoming.priority === 5) {
    return true;
  }
  if (current.sourceType === "chat_tts") {
    return false;
  }
  if (incoming.sourceType === "chat_tts") {
    return incoming.priority > current.priority;
  }
  return incoming.interruptCurrent && incoming.priority > current.priority;
}

function isRuntimeRegistryKey(key: string): boolean {
  return (
    [
    "booting",
    "warming_up",
    "thinking",
    "analyzing",
    "searching",
    "reading_webpage",
    "processing",
    "tool_calling",
    "retrying",
    "error",
    "recovered",
    "idle_return",
    "sleeping",
    ] as string[]
  ).includes(key);
}

function isPlaybackItem(value: VoicePlaybackItem | null): value is VoicePlaybackItem {
  return Boolean(value);
}

function compactSpeechText(text: string): string {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function looksUnsafeForStreamingSpeech(text: string): boolean {
  const value = String(text || "");
  return (
    value.includes("```") ||
    /<\/?[a-z][^>]*>/i.test(value) ||
    /[{}()[\];=<>]/.test(value) ||
    /\b(INFO|WARN|WARNING|ERROR|DEBUG|TRACE)\b/.test(value) ||
    (value.match(/\|/g) || []).length >= 2
  );
}

function takeCompletedSpeechSegments(buffer: string, includeTail = false): { segments: string[]; rest: string } {
  const source = String(buffer || "");
  const segments: string[] = [];
  const pattern = /[^。！？!?；;]+[。！？!?；;]+/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(source))) {
    const segment = compactSpeechText(match[0]);
    cursor = pattern.lastIndex;
    if (segment.length >= 3 && segment.length <= 180 && !looksUnsafeForStreamingSpeech(segment)) {
      segments.push(segment);
    }
  }
  let rest = source.slice(cursor);
  if (includeTail) {
    const tail = compactSpeechText(rest);
    if (tail.length >= 3 && tail.length <= 180 && !looksUnsafeForStreamingSpeech(tail)) {
      segments.push(tail);
      rest = "";
    }
  }
  return { segments, rest };
}

function toPlaybackItem(eventId: string, eventType: VoiceEvent["type"], entry: RuntimeVoiceEntry): VoicePlaybackItem | null {
  if (!entry.text.trim()) {
    return null;
  }
  return {
    id: `${eventId}:${entry.key}`,
    eventId,
    eventType,
    sourceType: eventType.startsWith("startup_") ? "startup_voice" : "runtime_voice",
    priority: entry.priority,
    kind: "tts",
    key: entry.key,
    preview: entry.text,
    interruptCurrent: entry.interruptCurrent,
    text: entry.text,
    systemVoice: true,
    dedupeKey: entry.key,
  };
}

export class FairyVoiceRuntime {
  private controller: VoicePlaybackController;
  private queue: VoicePlaybackItem[] = [];
  private cooldowns: Record<string, number> = {};
  private handledEventIds = new Set<string>();
  private currentState = "";
  private lastVoiceEvent = "";
  private lastVoiceKey = "";
  private lastSpeechMode: FairyVoiceRuntimeDebug["lastSpeechMode"] = "";
  private lastRenderedPreview = "";
  private lastRuntimeTransition = "";
  private lastPlaybackStage = "";
  private lastPlaybackDetail = "";
  private lastPlaybackError = "";
  private lastPlaybackItemKey = "";
  private lastPlaybackTimestampMs = 0;
  private playbackState: VoicePlaybackState = { current: null, queue: [] };
  private chatStreamBuffers: Record<string, string> = {};
  private chatStreamSpoken: Record<string, boolean> = {};
  private chatStreamChunkIndex: Record<string, number> = {};

  constructor(private readonly onDebugChange: (debug: FairyVoiceRuntimeDebug) => void) {
    this.controller = new VoicePlaybackController(
      (state) => {
        this.playbackState = state;
        this.flushDebug();
      },
      (diagnostic) => {
        this.applyPlaybackDiagnostic(diagnostic);
      },
    );
  }

  handleEvent(event: VoiceEvent): void {
    if (this.handledEventIds.has(event.id)) {
      return;
    }
    this.handledEventIds.add(event.id);
    this.lastVoiceEvent = event.type;

    if (event.type === "chat_reply_cancelled" || event.type === "chat_reply_error") {
      this.queue = this.queue.filter((item) => item.sourceType !== "chat_tts");
      if (this.playbackState.current?.sourceType === "chat_tts") {
        this.controller.stopCurrent();
      }
      if (event.requestId) {
        this.clearChatStream(event.requestId);
      } else {
        this.chatStreamBuffers = {};
        this.chatStreamSpoken = {};
        this.chatStreamChunkIndex = {};
      }
      this.controller.updateQueue(this.queue);
      this.flushDebug();
      return;
    }

    if (event.type === "runtime_state") {
      this.currentState = event.currentState;
      this.lastRuntimeTransition = event.previousState
        ? `${event.previousState} -> ${event.currentState}`
        : event.currentState;
      const entry = voiceForRuntimeStateChange(event.currentState, event.previousState);
      this.enqueue(entry ? [toPlaybackItem(event.id, event.type, entry)].filter(isPlaybackItem) : []);
      return;
    }

    if (event.type === "runtime_timeline") {
      const entry = voiceForTimelineEntry(event.entry);
      this.enqueue(entry ? [toPlaybackItem(event.id, event.type, entry)].filter(isPlaybackItem) : []);
      return;
    }

    if (event.type === "chat_reply_ready") {
      const { decision, playbackItem } = adaptChatReplyToPlayback(event.payload);
      this.lastSpeechMode = decision.mode;
      this.lastRenderedPreview = playbackItem?.preview || "";
      if (this.chatStreamSpoken[event.payload.requestId]) {
        const tailItem = this.flushChatStreamTail(event.payload.requestId);
        this.enqueue(tailItem ? [tailItem] : []);
        return;
      }
      this.clearChatStream(event.payload.requestId);
      this.enqueue(playbackItem ? [playbackItem] : []);
      return;
    }

    if (event.type === "chat_reply_chunk") {
      this.handleChatReplyChunk(event.requestId, event.text);
      this.flushDebug();
      return;
    }

    const lifecycleEntries = voicesForLifecycleEvent(event.type);
    this.enqueue(lifecycleEntries.map((entry) => toPlaybackItem(event.id, event.type, entry)).filter(isPlaybackItem));
  }

  shutdown(): void {
    this.queue = [];
    this.controller.shutdown();
    this.flushDebug();
  }

  private enqueue(items: VoicePlaybackItem[]): void {
    if (!items.length) {
      this.flushDebug();
      return;
    }
    const now = Date.now();
    for (const item of items) {
      const cooldownUntil = this.cooldowns[item.key] || 0;
      if (cooldownUntil > now) {
        continue;
      }
      if (this.queue.some((queued) => queued.dedupeKey === item.dedupeKey) || this.playbackState.current?.dedupeKey === item.dedupeKey) {
        continue;
      }
      if (shouldInterruptCurrent(this.playbackState.current, item)) {
        this.controller.stopCurrent();
        this.queue = this.queue.filter((queued) => queued.priority >= item.priority);
      }
      this.queue.push(item);
    }
    this.queue.sort((left, right) => right.priority - left.priority);
    this.controller.updateQueue(this.queue);
    this.playNext();
  }

  private playNext(): void {
    if (this.playbackState.current || this.queue.length === 0) {
      this.flushDebug();
      return;
    }
    const next = this.queue.shift();
    if (!next) {
      this.flushDebug();
      return;
    }
    this.cooldowns[next.key] = Date.now() + this.cooldownFor(next.priority, next.key);
    this.lastVoiceKey = next.key;
    if (next.speechMode) {
      this.lastSpeechMode = next.speechMode;
    }
    if (next.preview) {
      this.lastRenderedPreview = next.preview;
    }
    this.controller.updateQueue(this.queue);
    this.controller.play(next, () => this.playNext());
    this.flushDebug();
  }

  private handleChatReplyChunk(requestId: string, text: string): void {
    const normalizedRequestId = requestId || "pending";
    const nextBuffer = `${this.chatStreamBuffers[normalizedRequestId] || ""}${text || ""}`;
    const { segments, rest } = takeCompletedSpeechSegments(nextBuffer);
    this.chatStreamBuffers[normalizedRequestId] = rest;
    if (!segments.length) {
      return;
    }
    this.chatStreamSpoken[normalizedRequestId] = true;
    const items = segments.map((segment) => this.toChatStreamPlaybackItem(normalizedRequestId, segment, false));
    this.enqueue(items);
  }

  private flushChatStreamTail(requestId: string): VoicePlaybackItem | null {
    const normalizedRequestId = requestId || "pending";
    const buffer = this.chatStreamBuffers[normalizedRequestId] || "";
    const { segments } = takeCompletedSpeechSegments(buffer, true);
    if (!segments.length) {
      this.clearChatStream(normalizedRequestId);
      return null;
    }
    const item = this.toChatStreamPlaybackItem(normalizedRequestId, segments.join(" "), true);
    this.clearChatStream(normalizedRequestId);
    return item;
  }

  private clearChatStream(requestId: string): void {
    const normalizedRequestId = requestId || "pending";
    delete this.chatStreamBuffers[normalizedRequestId];
    delete this.chatStreamSpoken[normalizedRequestId];
    delete this.chatStreamChunkIndex[normalizedRequestId];
  }

  private toChatStreamPlaybackItem(requestId: string, text: string, isTail: boolean): VoicePlaybackItem {
    const index = (this.chatStreamChunkIndex[requestId] || 0) + 1;
    this.chatStreamChunkIndex[requestId] = index;
    const key = `chat_tts_stream:${requestId}:${index}${isTail ? ":tail" : ""}`;
    return {
      id: key,
      eventId: `chat-stream-${requestId}`,
      eventType: "chat_reply_chunk",
      sourceType: "chat_tts",
      priority: 4,
      kind: "tts",
      key,
      preview: text,
      interruptCurrent: false,
      speechMode: "full_read",
      text,
      systemVoice: false,
      dedupeKey: key,
    };
  }

  private cooldownFor(priority: VoicePriority, key: string): number {
    const normalizedKey = key.includes(":") ? key.split(":")[0] || key : key;
    if (isRuntimeRegistryKey(normalizedKey)) {
      return getRuntimeVoiceEntry(normalizedKey as FairyRuntimeVoiceKey).cooldownMs;
    }
    if (priority >= 4) {
      return 12000;
    }
    if (priority >= 3) {
      return 18000;
    }
    return 9000;
  }

  private flushDebug(): void {
    const now = Date.now();
    const cooldowns = Object.entries(this.cooldowns)
      .map(([key, until]) => ({ key, remainingMs: Math.max(0, until - now) }))
      .filter((item) => item.remainingMs > 0)
      .sort((left, right) => right.remainingMs - left.remainingMs)
      .slice(0, 8);
    this.onDebugChange({
      currentRuntimeState: this.currentState,
      lastVoiceEvent: this.lastVoiceEvent,
      lastVoiceKey: this.lastVoiceKey,
      currentSpeakingSource: this.playbackState.current?.sourceType || "",
      queueLength: this.queue.length + (this.playbackState.current ? 1 : 0),
      cooldowns,
      lastSpeechMode: this.lastSpeechMode,
      lastRenderedSpokenTextPreview: this.lastRenderedPreview,
      lastRuntimeTransition: this.lastRuntimeTransition,
      lastPlaybackStage: this.lastPlaybackStage,
      lastPlaybackDetail: this.lastPlaybackDetail,
      lastPlaybackError: this.lastPlaybackError,
      lastPlaybackItemKey: this.lastPlaybackItemKey,
      lastPlaybackTimestampMs: this.lastPlaybackTimestampMs,
    });
  }

  private applyPlaybackDiagnostic(diagnostic: VoicePlaybackDiagnostic): void {
    this.lastPlaybackStage = diagnostic.stage;
    this.lastPlaybackDetail = diagnostic.detail;
    this.lastPlaybackError = diagnostic.error;
    this.lastPlaybackItemKey = diagnostic.itemKey;
    this.lastPlaybackTimestampMs = diagnostic.timestampMs;
    if (diagnostic.preview) {
      this.lastRenderedPreview = diagnostic.preview;
    }
    this.flushDebug();
  }
}
