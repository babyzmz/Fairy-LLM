import type { RuntimeAssistantState } from "../../../lib/types/api";
import type { VoicePriority, VoiceTimelineEntryLike } from "./voiceTypes";

export type FairyRuntimeVoiceKey =
  | "booting"
  | "warming_up"
  | "thinking"
  | "analyzing"
  | "searching"
  | "reading_webpage"
  | "processing"
  | "tool_calling"
  | "retrying"
  | "error"
  | "recovered"
  | "idle_return"
  | "sleeping";

export interface RuntimeVoiceEntry {
  key: FairyRuntimeVoiceKey;
  text: string;
  cooldownMs: number;
  priority: VoicePriority;
  interruptCurrent: boolean;
}

const VOICE_ENTRIES: Record<FairyRuntimeVoiceKey, RuntimeVoiceEntry> = {
  booting: {
    key: "booting",
    text: "系统启动中。",
    cooldownMs: 45000,
    priority: 3,
    interruptCurrent: false,
  },
  warming_up: {
    key: "warming_up",
    text: "正在同步运行状态。",
    cooldownMs: 18000,
    priority: 2,
    interruptCurrent: false,
  },
  thinking: {
    key: "thinking",
    text: "正在思考。",
    cooldownMs: 12000,
    priority: 2,
    interruptCurrent: false,
  },
  analyzing: {
    key: "analyzing",
    text: "正在分析内容。",
    cooldownMs: 9000,
    priority: 2,
    interruptCurrent: false,
  },
  searching: {
    key: "searching",
    text: "正在检索网络信息。",
    cooldownMs: 7000,
    priority: 2,
    interruptCurrent: false,
  },
  reading_webpage: {
    key: "reading_webpage",
    text: "正在分析网页内容。",
    cooldownMs: 7000,
    priority: 2,
    interruptCurrent: false,
  },
  processing: {
    key: "processing",
    text: "正在处理请求。",
    cooldownMs: 7000,
    priority: 2,
    interruptCurrent: false,
  },
  tool_calling: {
    key: "tool_calling",
    text: "正在执行操作。",
    cooldownMs: 7000,
    priority: 2,
    interruptCurrent: false,
  },
  retrying: {
    key: "retrying",
    text: "正在重新尝试。",
    cooldownMs: 9000,
    priority: 2,
    interruptCurrent: false,
  },
  error: {
    key: "error",
    text: "当前操作出现异常。",
    cooldownMs: 12000,
    priority: 5,
    interruptCurrent: true,
  },
  recovered: {
    key: "recovered",
    text: "主人，系统已恢复在线。",
    cooldownMs: 25000,
    priority: 3,
    interruptCurrent: false,
  },
  idle_return: {
    key: "idle_return",
    text: "核心链路运行正常。",
    cooldownMs: 25000,
    priority: 1,
    interruptCurrent: false,
  },
  sleeping: {
    key: "sleeping",
    text: "正在进入待机状态。",
    cooldownMs: 18000,
    priority: 1,
    interruptCurrent: false,
  },
};

export function getRuntimeVoiceEntry(key: FairyRuntimeVoiceKey): RuntimeVoiceEntry {
  return VOICE_ENTRIES[key];
}

export function shouldPlayVoiceForStateTransition(
  previousState: RuntimeAssistantState | null,
  nextState: RuntimeAssistantState,
): boolean {
  if (previousState === nextState) {
    return false;
  }
  if (previousState === null) {
    return nextState === "booting" || nextState === "warming_up" || nextState === "idle";
  }
  const transitionKey = `${previousState}->${nextState}`;
  const allowed = new Set<string>([
    "booting->warming_up",
    "booting->idle",
    "warming_up->idle",
    "idle->thinking",
    "thinking->analyzing",
    "analyzing->replying",
    "any->error",
    "error->idle",
    "active->sleeping",
    "sleeping->booting",
  ]);
  if (nextState === "error") {
    return true;
  }
  if (nextState === "sleeping") {
    return ["thinking", "analyzing", "replying", "idle", "error", "warming_up"].includes(previousState);
  }
  if (previousState === "sleeping" && (nextState === "booting" || nextState === "warming_up" || nextState === "idle")) {
    return true;
  }
  return allowed.has(transitionKey);
}

export function voiceForRuntimeStateChange(
  state: RuntimeAssistantState,
  previousState: RuntimeAssistantState | null,
): RuntimeVoiceEntry | null {
  if (!shouldPlayVoiceForStateTransition(previousState, state)) {
    return null;
  }
  if (state === "booting") {
    return VOICE_ENTRIES.booting;
  }
  if (state === "warming_up") {
    return VOICE_ENTRIES.warming_up;
  }
  if (state === "thinking") {
    return VOICE_ENTRIES.thinking;
  }
  if (state === "analyzing") {
    return VOICE_ENTRIES.analyzing;
  }
  if (state === "error") {
    return VOICE_ENTRIES.error;
  }
  if (state === "sleeping") {
    return VOICE_ENTRIES.sleeping;
  }
  if (state === "idle") {
    if (previousState && ["error", "sleeping", "booting", "warming_up"].includes(previousState)) {
      return VOICE_ENTRIES.recovered;
    }
    if (previousState && ["replying", "analyzing", "thinking"].includes(previousState)) {
      return VOICE_ENTRIES.idle_return;
    }
  }
  return null;
}

export function voiceForTimelineEntry(entry: VoiceTimelineEntryLike | null): RuntimeVoiceEntry | null {
  if (!entry) {
    return null;
  }
  const summary = String(entry.summary || "").trim().toLowerCase();
  if (!summary) {
    return null;
  }
  if (summary === "orchestration_step_retry" || summary.includes("retry")) {
    return VOICE_ENTRIES.retrying;
  }
  if (summary === "desktop_action_dispatch") {
    return VOICE_ENTRIES.tool_calling;
  }
  if (summary === "progress: searching") {
    return VOICE_ENTRIES.searching;
  }
  if (summary === "progress: reading") {
    return VOICE_ENTRIES.reading_webpage;
  }
  if (summary === "progress: summarizing" || summary === "progress: finalizing" || summary === "progress: planning") {
    return VOICE_ENTRIES.processing;
  }
  return null;
}

export function voicesForLifecycleEvent(
  event: "startup_booting" | "startup_ready" | "startup_reused" | "startup_timeout" | "startup_stopped" | "startup_failed",
): RuntimeVoiceEntry[] {
  if (event === "startup_booting") {
    return [VOICE_ENTRIES.booting, VOICE_ENTRIES.processing];
  }
  if (event === "startup_ready" || event === "startup_reused") {
    return [VOICE_ENTRIES.recovered];
  }
  if (event === "startup_timeout" || event === "startup_failed") {
    return [VOICE_ENTRIES.error];
  }
  if (event === "startup_stopped") {
    return [VOICE_ENTRIES.sleeping];
  }
  return [];
}
