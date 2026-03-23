import { chatActions, useChatStore } from "../../lib/stores/chatStore";

function formatTimestamp(timestampMs?: number): string {
  if (!timestampMs) {
    return "--";
  }
  return new Date(timestampMs).toLocaleTimeString();
}

export function SystemPanel(): JSX.Element {
  const systemState = useChatStore((state) => state.systemState);
  const systemPanelError = useChatStore((state) => state.systemPanelError);
  const backendControlBusy = useChatStore((state) => state.backendControlBusy);
  const systemPanelOpen = useChatStore((state) => state.systemPanelOpen);
  const bridgeEvents = systemState?.bridge_events ?? [];
  const runtimeEvents = systemState?.runtime_state?.recent_events ?? [];
  const recentEvents = [
    ...bridgeEvents.map((event) => ({
      id: `bridge-${event.timestamp_ms}-${event.status}`,
      timestampMs: event.timestamp_ms,
      label: `bridge:${event.status}`,
      detail: event.message,
    })),
    ...runtimeEvents.map((event) => ({
      id: `runtime-${event.timestamp_ms}-${event.event}`,
      timestampMs: event.timestamp_ms,
      label: `runtime:${event.event}`,
      detail: String(event.detail?.message || event.detail?.status || event.request_id || ""),
    })),
  ]
    .sort((left, right) => right.timestampMs - left.timestampMs)
    .slice(0, 12);

  return (
    <details
      className="system-panel"
      open={systemPanelOpen}
      onToggle={(event) => chatActions.setSystemPanelOpen((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary className="system-panel__summary">System Panel</summary>
      <div className="system-panel__content">
        <div className="system-grid">
          <div>
            <div className="system-label">Bridge</div>
            <div className="system-value">{systemState?.bridge_status || "unknown"}</div>
          </div>
          <div>
            <div className="system-label">Runtime</div>
            <div className="system-value">{systemState?.runtime_state?.backend_status || "unknown"}</div>
          </div>
          <div>
            <div className="system-label">Streaming</div>
            <div className="system-value">{systemState?.runtime_state?.is_streaming ? "active" : "idle"}</div>
          </div>
          <div>
            <div className="system-label">Active Request</div>
            <div className="system-value">{systemState?.runtime_state?.active_stream_request || "--"}</div>
          </div>
        </div>

        <div className="system-actions">
          <button type="button" disabled={backendControlBusy} onClick={() => void chatActions.runSystemAction("cancel_current_request")}>
            Cancel
          </button>
          <button type="button" disabled={backendControlBusy} onClick={() => void chatActions.runSystemAction("restart_backend")}>
            Restart backend
          </button>
          <button type="button" disabled={backendControlBusy} onClick={() => void chatActions.runSystemAction("refresh_capabilities")}>
            Refresh capabilities
          </button>
          <button type="button" disabled={backendControlBusy} onClick={() => void chatActions.runSystemAction("clear_asset_cache")}>
            Clear asset cache
          </button>
        </div>

        {systemPanelError ? <div className="system-panel__error">{systemPanelError}</div> : null}

        <div className="system-panel__events">
          <div className="system-label">Recent system events</div>
          {recentEvents.length === 0 ? (
            <div className="system-panel__empty">No system events recorded yet.</div>
          ) : (
            recentEvents.map((event) => (
              <div key={event.id} className="system-event">
                <span className="system-event__time">{formatTimestamp(event.timestampMs)}</span>
                <span className="system-event__label">{event.label}</span>
                <span className="system-event__detail">{event.detail || "--"}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </details>
  );
}
