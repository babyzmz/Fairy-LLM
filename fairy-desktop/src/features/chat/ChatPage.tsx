import { useEffect } from "react";

import { chatActions, useChatStore } from "../../lib/stores/chatStore";
import { FairyAvatar } from "../../components/fairy/FairyAvatar";
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
  const streamError = useChatStore((state) => state.streamError);
  const lastError = useChatStore((state) => state.lastError);
  const sessionId = useChatStore((state) => state.sessionId);
  const didFallbackToInvoke = useChatStore((state) => state.didFallbackToInvoke);
  const wasCancelled = useChatStore((state) => state.wasCancelled);
  const streamTimeline = useChatStore((state) => state.streamTimeline);
  const desktopNotification = useChatStore((state) => state.desktopNotification);

  useEffect(() => {
    void chatActions.probeBackend();
    void chatActions.loadSystemState();
  }, []);

  useEffect(() => {
    let dispose: (() => void) | undefined;
    const setup = async () => {
      try {
        const { listen } = await import("@tauri-apps/api/event");
        const unlistenStopped = await listen<{ message?: string }>("backend://stopped", (event) => {
          chatActions.recordBackendLifecycle("backend stopped", event.payload?.message || "");
          chatActions.markBackendOffline(event.payload?.message || "Backend stopped.");
        });
        const unlistenFailed = await listen<{ message?: string }>("backend://spawn-failed", (event) => {
          chatActions.recordBackendLifecycle("backend spawn failed", event.payload?.message || "");
          chatActions.markBackendOffline(event.payload?.message || "Backend failed to start.");
        });
        const unlistenStarting = await listen<{ message?: string }>("backend://starting", (event) => {
          chatActions.recordBackendLifecycle("backend starting", event.payload?.message || "");
        });
        const unlistenTimeout = await listen<{ message?: string }>("backend://timeout", (event) => {
          chatActions.recordBackendLifecycle("backend startup timeout", event.payload?.message || "");
        });
        const unlistenReused = await listen<{ message?: string }>("backend://reused", (event) => {
          chatActions.recordBackendLifecycle("backend reused", event.payload?.message || "");
        });
        const unlistenReady = await listen("backend://ready", () => {
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
  }, []);

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="app-header__identity">
          <FairyAvatar size={84} animated mode={isStreaming ? "active" : "idle"} />
          <div>
            <h1>Fairy Desktop</h1>
            <p>Tauri shell connected to the local Fairy runtime API.</p>
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

      <MessageList messages={messages} />
      <SystemPanel />
      <DebugTimelinePanel entries={streamTimeline} />
      <Composer
        disabled={isSending || backendStatus === "offline"}
        streaming={isStreaming}
        onSend={chatActions.sendMessage}
        onCancel={() => chatActions.interruptStreaming("Interrupted by user.")}
      />
    </main>
  );
}
