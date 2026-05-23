import { firstCardAction } from "../../../lib/actions/cardActions";
import type { CompareCardEnvelope } from "../../../lib/types/api";
import { ActionButton } from "./ActionButton";

interface CompareCardProps {
  card: CompareCardEnvelope;
}

export function CompareCard({ card }: CompareCardProps): JSX.Element {
  const items = card.data.items ?? [];
  const differences = card.data.differences ?? [];
  const sharedPoints = card.data.shared_points ?? [];
  const sourceAction = firstCardAction(card.actions, ["open_source", "open_url"]);

  return (
    <article className="card card--compare">
      <div className="card__header">
        <div>
          <h3>{card.data.title || "Comparison"}</h3>
          {card.data.summary ? <p>{card.data.summary}</p> : null}
        </div>
      </div>
      {items.length > 0 ? (
        <div className="card__stack">
          {items.map((item) => (
            <section className="compare-card__item" key={item.title}>
              <div className="compare-card__item-head">
                <strong>{item.title}</strong>
                <ActionButton action={firstCardAction(item.actions, ["open_url"])} className="card__inline-action" />
              </div>
              {item.summary ? <p>{item.summary}</p> : null}
              {item.highlights && item.highlights.length > 0 ? (
                <ul className="card__bullet-list">
                  {item.highlights.map((highlight) => (
                    <li key={`${item.title}-${highlight}`}>{highlight}</li>
                  ))}
                </ul>
              ) : null}
            </section>
          ))}
        </div>
      ) : null}
      {differences.length > 0 ? (
        <section className="card__section">
          <h4>Differences</h4>
          <ul className="card__bullet-list">
            {differences.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {sharedPoints.length > 0 ? (
        <section className="card__section">
          <h4>Shared</h4>
          <ul className="card__bullet-list">
            {sharedPoints.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {card.data.recommendation ? <p className="compare-card__recommendation">{card.data.recommendation}</p> : null}
      {sourceAction ? (
        <div className="card__footer">
          <ActionButton action={sourceAction} className="card__source-button" label={card.data.source_label || sourceAction.label} />
        </div>
      ) : null}
    </article>
  );
}
