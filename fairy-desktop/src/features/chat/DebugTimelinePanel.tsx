import type { StreamTimelineEntry } from "../../lib/stores/chatStore";

interface DebugTimelinePanelProps {
  entries: StreamTimelineEntry[];
}

export function DebugTimelinePanel({ entries }: DebugTimelinePanelProps): JSX.Element {
  const visibleEntries = entries.slice(-80).reverse();

  return (
    <details className="debug-panel">
      <summary className="debug-panel__summary">
        Debug timeline
        <span className="debug-panel__count">{entries.length}</span>
      </summary>
      <div className="debug-panel__body">
        {visibleEntries.length === 0 ? (
          <div className="debug-panel__empty">No stream trace captured yet.</div>
        ) : (
          <div className="debug-panel__timeline">
            {visibleEntries.map((entry) => (
              <article className="debug-panel__entry" key={entry.id}>
                <div className="debug-panel__entry-head">
                  <strong>{entry.event}</strong>
                  <span>req={entry.requestId || "-"}</span>
                  <span>seq={entry.sequence || 0}</span>
                  <span>{new Date(entry.timestampMs).toLocaleTimeString()}</span>
                </div>
                <div className="debug-panel__entry-summary">{entry.summary}</div>
                {entry.detail ? <pre className="debug-panel__entry-detail">{entry.detail}</pre> : null}
              </article>
            ))}
          </div>
        )}
      </div>
    </details>
  );
}
