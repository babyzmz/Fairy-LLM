import { useCallback, useEffect, useRef, useState } from "react";

import type { AssistantFinalMessage, BackendStatus } from "../../../lib/stores/chatStore";
import type { DesktopSystemState, RuntimeAssistantState } from "../../../lib/types/api";
import {
  createChatReplyCancelledVoiceEvent,
  createChatReplyChunkVoiceEvent,
  createChatReplyErrorVoiceEvent,
  createChatReplyReadyVoiceEvent,
  createLifecycleVoiceEvent,
  createRuntimeStateVoiceEvent,
  createTimelineVoiceEvent,
} from "./voiceEvents";
import { FairyVoiceRuntime } from "./voiceRuntime";
import type { FairyVoiceRuntimeDebug, VoiceTimelineEntryLike } from "./voiceTypes";

type LifecycleVoiceEvent = "starting" | "ready" | "reused" | "timeout" | "stopped" | "spawn-failed";

interface UseFairyVoiceRuntimeInput {
  backendStatus: BackendStatus;
  systemState: DesktopSystemState | null;
}

const INITIAL_DEBUG: FairyVoiceRuntimeDebug = {
  currentRuntimeState: "",
  lastVoiceEvent: "",
  lastVoiceKey: "",
  currentSpeakingSource: "",
  queueLength: 0,
  cooldowns: [],
  lastSpeechMode: "",
  lastRenderedSpokenTextPreview: "",
  lastRuntimeTransition: "",
  lastPlaybackStage: "",
  lastPlaybackDetail: "",
  lastPlaybackError: "",
  lastPlaybackItemKey: "",
  lastPlaybackTimestampMs: 0,
};

export function useFairyVoiceRuntime({ backendStatus, systemState }: UseFairyVoiceRuntimeInput): {
  handleLifecycleEvent: (event: LifecycleVoiceEvent) => void;
  handleStateChange: (runtimeState: RuntimeAssistantState | null) => void;
  handleTimelineEntry: (entry: VoiceTimelineEntryLike | null) => void;
  handleChatReplyChunk: (requestId: string, text: string) => void;
  handleChatReply: (message: AssistantFinalMessage | null) => void;
  handleChatReplyCancelled: (requestId?: string, reason?: string) => void;
  handleChatReplyError: (requestId?: string, message?: string) => void;
  debug: FairyVoiceRuntimeDebug;
} {
  const [debug, setDebug] = useState<FairyVoiceRuntimeDebug>(INITIAL_DEBUG);
  const runtimeRef = useRef<FairyVoiceRuntime | null>(null);
  const lastStateRef = useRef<RuntimeAssistantState | null>(null);

  if (!runtimeRef.current) {
    runtimeRef.current = new FairyVoiceRuntime(setDebug);
  }

  const handleLifecycleEvent = useCallback((event: LifecycleVoiceEvent) => {
    runtimeRef.current?.handleEvent(createLifecycleVoiceEvent(event));
  }, []);

  const handleStateChange = useCallback((runtimeState: RuntimeAssistantState | null) => {
    if (!runtimeState) {
      return;
    }
    const previousState = lastStateRef.current;
    if (runtimeState === previousState) {
      return;
    }
    lastStateRef.current = runtimeState;
    runtimeRef.current?.handleEvent(createRuntimeStateVoiceEvent(runtimeState, previousState));
  }, []);

  const handleTimelineEntry = useCallback((entry: VoiceTimelineEntryLike | null) => {
    if (!entry) {
      return;
    }
    runtimeRef.current?.handleEvent(createTimelineVoiceEvent(entry));
  }, []);

  const handleChatReplyChunk = useCallback((requestId: string, text: string) => {
    if (!text.trim()) {
      return;
    }
    runtimeRef.current?.handleEvent(createChatReplyChunkVoiceEvent(requestId, text));
  }, []);

  const handleChatReply = useCallback((message: AssistantFinalMessage | null) => {
    if (!message) {
      return;
    }
    runtimeRef.current?.handleEvent(
      createChatReplyReadyVoiceEvent({
        requestId: message.requestId,
        text: message.text,
        cards: message.cards,
        speech: (message.meta?.speech as { mode?: string; text?: string; allow_streaming?: boolean } | undefined) ?? undefined,
        meta: message.meta,
      }),
    );
  }, []);

  const handleChatReplyCancelled = useCallback((requestId?: string, reason?: string) => {
    runtimeRef.current?.handleEvent(createChatReplyCancelledVoiceEvent(requestId, reason));
  }, []);

  const handleChatReplyError = useCallback((requestId?: string, message?: string) => {
    runtimeRef.current?.handleEvent(createChatReplyErrorVoiceEvent(requestId, message));
  }, []);

  useEffect(() => {
    if (backendStatus === "offline") {
      lastStateRef.current = "sleeping";
    }
  }, [backendStatus]);

  useEffect(() => {
    return () => {
      runtimeRef.current?.shutdown();
      runtimeRef.current = null;
    };
  }, []);

  useEffect(() => {
    setDebug((previous) => ({
      ...previous,
      currentRuntimeState: String(systemState?.runtime_state?.current_state || previous.currentRuntimeState || ""),
      lastRuntimeTransition:
        (() => {
          const trace = systemState?.runtime_state?.runtime_state_trace ?? [];
          const last = trace[trace.length - 1];
          if (!last) {
            return previous.lastRuntimeTransition;
          }
          const reason = String(last.reason || "").trim();
          return reason
            ? `${last.previous_state} -> ${last.current_state} (${reason})`
            : `${last.previous_state} -> ${last.current_state}`;
        })(),
    }));
  }, [systemState?.runtime_state?.current_state, systemState?.runtime_state?.runtime_state_trace]);

  return {
    handleLifecycleEvent,
    handleStateChange,
    handleTimelineEntry,
    handleChatReplyChunk,
    handleChatReply,
    handleChatReplyCancelled,
    handleChatReplyError,
    debug,
  };
}
