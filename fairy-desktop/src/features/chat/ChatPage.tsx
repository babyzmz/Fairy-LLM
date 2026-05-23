import { useEffect, useMemo, useState } from "react";

import { persistChatAttachments } from "../../lib/api/system";
import { chatActions, useChatStore } from "../../lib/stores/chatStore";
import { FairyAvatar } from "../../components/fairy/FairyAvatar";
import { deriveFairyPresenceState } from "../../components/fairy/fairyPresenceState";
import { useFairyVoiceRuntime } from "../../components/fairy/useFairyVoiceRuntime";
import { ActivityPanel } from "./ActivityPanel";
import { buildActivityData } from "./activity";
import { Composer } from "./Composer";
import { DebugTimelinePanel } from "./DebugTimelinePanel";
import { MessageList } from "./MessageList";
import { SystemPanel } from "./SystemPanel";

export function ChatPage(): JSX.Element {
  const backendStatus = useChatStore((state) => state.backendStatus);
  const capabilities = useChatStore((state) => state.capabilities);
  const messages = useChatStore((state) => state.messages);
  const isSending = useChatStore((state) => state.isSending);
  const isStreaming = useChatStore((state) => state.isStreaming);
  const partialAssistantText = useChatStore((state) => state.partialAssistantText);
  const streamedCards = useChatStore((state) => state.streamedCards);
  const streamError = useChatStore((state) => state.streamError);
  const lastError = useChatStore((state) => state.lastError);
  const sessionId = useChatStore((state) => state.sessionId);
  const didFallbackToInvoke = useChatStore((state) => state.didFallbackToInvoke);
  const wasCancelled = useChatStore((state) => state.wasCancelled);
  const streamTimeline = useChatStore((state) => state.streamTimeline);
  const streamRequestId = useChatStore((state) => state.streamRequestId);
  const desktopNotification = useChatStore((state) => state.desktopNotification);
  const systemState = useChatStore((state) => state.systemState);
  const systemPanelOpen = useChatStore((state) => state.systemPanelOpen);
  const [activityRequestId, setActivityRequestId] = useState("");
  const latestTimelineEntry = streamTimeline[streamTimeline.length - 1] ?? null;
  const latestAssistantFinalMessage = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (message?.kind === "assistant_final") {
        return message;
      }
    }
    return null;
  }, [messages]);
  const selectedActivityMessage = useMemo(() => {
    if (!activityRequestId) {
      return null;
    }
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if ("requestId" in message && message.requestId === activityRequestId) {
        return message;
      }
    }
    return null;
  }, [activityRequestId, messages]);
  const selectedActivity = useMemo(
    () => buildActivityData(selectedActivityMessage, streamTimeline),
    [selectedActivityMessage, streamTimeline],
  );

  const fairyPresence = deriveFairyPresenceState({
    backendStatus,
    systemState,
    isSending,
    isStreaming,
    partialAssistantText,
    streamedCards,
    streamError,
    lastError,
    desktopNotification,
    systemPanelOpen,
    streamTimeline,
  });
  const {
    handleLifecycleEvent,
    handleStateChange,
    handleTimelineEntry,
    handleChatReplyChunk,
    handleChatReply,
    handleChatReplyCancelled,
    handleChatReplyError,
    debug: fairyVoiceDebug,
  } = useFairyVoiceRuntime({
    backendStatus,
    systemState,
  });

  useEffect(() => {
    void chatActions.probeBackend();
    void chatActions.loadSystemState();
  }, []);

  useEffect(() => {
    if (!(isSending || isStreaming)) {
      return;
    }
    const timer = window.setInterval(() => {
      void chatActions.loadSystemState();
    }, 800);
    return () => {
      window.clearInterval(timer);
    };
  }, [isSending, isStreaming]);

  useEffect(() => {
    handleStateChange(systemState?.runtime_state?.current_state ?? null);
  }, [handleStateChange, systemState?.runtime_state?.current_state]);

  useEffect(() => {
    handleTimelineEntry(latestTimelineEntry);
  }, [handleTimelineEntry, latestTimelineEntry]);

  useEffect(() => {
    if (latestTimelineEntry?.event !== "text_delta") {
      return;
    }
    handleChatReplyChunk(latestTimelineEntry.requestId, latestTimelineEntry.detail || "");
  }, [handleChatReplyChunk, latestTimelineEntry]);

  useEffect(() => {
    handleChatReply(latestAssistantFinalMessage);
  }, [handleChatReply, latestAssistantFinalMessage?.id]);

  useEffect(() => {
    if (wasCancelled) {
      handleChatReplyCancelled(streamRequestId || undefined, "stream_cancelled");
    }
  }, [handleChatReplyCancelled, streamRequestId, wasCancelled]);

  useEffect(() => {
    if (streamError) {
      handleChatReplyError(streamRequestId || undefined, streamError);
    }
  }, [handleChatReplyError, streamError, streamRequestId]);

  useEffect(() => {
    let dispose: (() => void) | undefined;
    const setup = async () => {
      try {
        const { listen } = await import("@tauri-apps/api/event");
        const unlistenStopped = await listen<{ message?: string }>("backend://stopped", (event) => {
          handleLifecycleEvent("stopped");
          chatActions.recordBackendLifecycle("backend stopped", event.payload?.message || "");
          chatActions.markBackendOffline(event.payload?.message || "Backend stopped.");
        });
        const unlistenFailed = await listen<{ message?: string }>("backend://spawn-failed", (event) => {
          handleLifecycleEvent("spawn-failed");
          chatActions.recordBackendLifecycle("backend spawn failed", event.payload?.message || "");
          chatActions.markBackendOffline(event.payload?.message || "Backend failed to start.");
        });
        const unlistenStarting = await listen<{ message?: string }>("backend://starting", (event) => {
          handleLifecycleEvent("starting");
          chatActions.recordBackendLifecycle("backend starting", event.payload?.message || "");
        });
        const unlistenTimeout = await listen<{ message?: string }>("backend://timeout", (event) => {
          handleLifecycleEvent("timeout");
          chatActions.recordBackendLifecycle("backend startup timeout", event.payload?.message || "");
        });
        const unlistenReused = await listen<{ message?: string }>("backend://reused", (event) => {
          handleLifecycleEvent("reused");
          chatActions.recordBackendLifecycle("backend reused", event.payload?.message || "");
        });
        const unlistenReady = await listen("backend://ready", () => {
          handleLifecycleEvent("ready");
          chatActions.recordBackendLifecycle("backend ready");
          void chatActions.probeBackend();
          void chatActions.loadSystemState();
        });
        const unlistenDesktopNotification = await listen<{ message?: string }>("desktop://notification", (event) => {
          chatActions.showDesktopNotification(event.payload?.message || "Desktop notification");
        });
        const unlistenOpenPanel = await listen<{ panel?: string }>("desktop://open-panel", () => {
          chatActions.setSystemPanelOpen(true);
        });
        dispose = () => {
          unlistenStopped();
          unlistenFailed();
          unlistenStarting();
          unlistenTimeout();
          unlistenReused();
          unlistenReady();
          unlistenDesktopNotification();
          unlistenOpenPanel();
        };
      } catch {
        dispose = undefined;
      }
    };
    void setup();
    return () => {
      dispose?.();
    };
  }, [handleLifecycleEvent]);

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="app-header__identity">
          <FairyAvatar size={84} animated mode={fairyPresence.mode} signal={fairyPresence.signal} />
          <div>
            <h1>Fairy Desktop</h1>
            <p>Tauri shell connected to the local Fairy runtime API.</p>
            <div className={`presence-state presence-state--${fairyPresence.mode}`}>
              <span className="presence-state__label">{fairyPresence.label}</span>
              <span className="presence-state__detail">{fairyPresence.detail}</span>
            </div>
          </div>
        </div>
        <div className={`status-pill status-pill--${backendStatus}`}>
          <span className="status-pill__dot" />
          backend {backendStatus}
        </div>
      </header>

      <section className="info-strip">
        <div>Session: {sessionId}</div>
        <div>Cards: {capabilities?.cards.join(", ") || "loading..."}</div>
        <div>Streaming: {capabilities?.streaming ? "on" : "off"}</div>
      </section>

      {lastError ? <div className="banner banner--error">{lastError}</div> : null}
      {streamError && streamError !== lastError ? <div className="banner banner--error">{streamError}</div> : null}
      {desktopNotification ? <div className="banner">{desktopNotification}</div> : null}
      {didFallbackToInvoke ? <div className="banner">Stream fallback: /chat/invoke</div> : null}
      {wasCancelled ? <div className="banner">The previous stream was cancelled.</div> : null}

      <MessageList messages={messages} streamTimeline={streamTimeline} onOpenActivity={setActivityRequestId} />
      <SystemPanel voiceDebug={fairyVoiceDebug} lastAvatarMode={fairyPresence.mode} />
      <DebugTimelinePanel entries={streamTimeline} />
      <ActivityPanel activity={selectedActivity} open={Boolean(activityRequestId)} onClose={() => setActivityRequestId("")} />
      <Composer
        disabled={isSending || backendStatus === "offline"}
        streaming={isStreaming}
        onSend={async (message, attachments) => {
          const persistedAttachments = await persistChatAttachments(attachments);
          await chatActions.sendMessage(message, persistedAttachments);
        }}
        onCancel={() => chatActions.interruptStreaming("Interrupted by user.")}
      />
    </main>
  );
}
