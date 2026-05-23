import { openExternalUrl } from "../../lib/actions/cardActions";
import type { ActivityData } from "./activity";

interface ActivityPanelProps {
  activity: ActivityData | null;
  open: boolean;
  onClose: () => void;
}

export function ActivityPanel({ activity, open, onClose }: ActivityPanelProps): JSX.Element | null {
  if (!open || !activity) {
    return null;
  }

  const visibleSources = activity.sources.slice(0, 6);
  const remainingSources = Math.max(0, activity.sources.length - visibleSources.length);

  return (
    <>
      <button className="activity-panel__backdrop" onClick={onClose} aria-label="Close activity panel" />
      <aside className="activity-panel" aria-label="Activity panel">
        <header className="activity-panel__header">
          <div>
            <div className="activity-panel__eyebrow">活动</div>
            <h2>{activity.durationLabel}</h2>
          </div>
          <button className="activity-panel__close" onClick={onClose} aria-label="Close activity panel" type="button">
            ×
          </button>
        </header>

        <section className="activity-panel__section">
          <h3>思考</h3>
          {activity.entries.length === 0 ? (
            <div className="activity-panel__empty">还没有可展开的活动过程。</div>
          ) : (
            <div className="activity-panel__entries">
              {activity.entries.map((entry, index) => (
                <article className="activity-panel__entry" key={`${entry.stage}-${index}`}>
                  <div className="activity-panel__entry-title">{entry.title}</div>
                  {entry.detail ? <div className="activity-panel__entry-detail">{entry.detail}</div> : null}
                </article>
              ))}
            </div>
          )}
        </section>

        {(visibleSources.length > 0 || activity.webAccess.finalPageUrl) && (
          <section className="activity-panel__section">
            <h3>来源</h3>
            {activity.webAccess.finalPageUrl ? (
              <div className="activity-panel__final">
                <div className="activity-panel__meta-row">
                  <span>最终页面</span>
                  <button className="activity-panel__link-button" type="button" onClick={() => openExternalUrl(activity.webAccess.finalPageUrl)}>
                    {activity.webAccess.finalPageUrl}
                  </button>
                </div>
                {activity.webAccess.finalPageType ? (
                  <div className="activity-panel__meta-row">
                    <span>页面类型</span>
                    <strong>{activity.webAccess.finalPageType}</strong>
                  </div>
                ) : null}
                {activity.webAccess.stopReason ? (
                  <div className="activity-panel__meta-row">
                    <span>停止原因</span>
                    <strong>{activity.webAccess.stopReason}</strong>
                  </div>
                ) : null}
              </div>
            ) : null}

            {visibleSources.length > 0 ? (
              <div className="activity-panel__sources">
                {visibleSources.map((source) => (
                  <button
                    className="activity-panel__source-chip"
                    type="button"
                    onClick={() => openExternalUrl(source.url)}
                    key={`${source.url}-${source.label}`}
                  >
                    {source.label}
                  </button>
                ))}
                {remainingSources > 0 ? <span className="activity-panel__more">再显示 {remainingSources} 个</span> : null}
              </div>
            ) : null}
          </section>
        )}

        {activity.webAccess.selectedLinks.length > 0 ? (
          <section className="activity-panel__section">
            <h3>点击路径</h3>
            <div className="activity-panel__entries">
              {activity.webAccess.selectedLinks.map((link, index) => (
                <article className="activity-panel__entry" key={`${link.url}-${index}`}>
                  <div className="activity-panel__entry-title">{link.text}</div>
                  <div className="activity-panel__entry-detail">{link.url}</div>
                </article>
              ))}
            </div>
          </section>
        ) : null}
      </aside>
    </>
  );
}
