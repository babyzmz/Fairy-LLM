import { firstCardAction } from "../../../lib/actions/cardActions";
import type { ReleaseCardEnvelope } from "../../../lib/types/api";
import { ActionButton } from "./ActionButton";

interface ReleaseCardProps {
  card: ReleaseCardEnvelope;
}

export function ReleaseCard({ card }: ReleaseCardProps): JSX.Element {
  const highlights = card.data.highlights ?? [];
  const sourceAction = firstCardAction(card.actions, ["open_source", "open_url"]);

  return (
    <article className="card card--release">
      <div className="card__header">
        <div>
          <h3>{card.data.title || "Release Update"}</h3>
          {card.data.summary ? <p>{card.data.summary}</p> : null}
        </div>
      </div>
      <dl className="card__meta-grid">
        {card.data.date ? (
          <div>
            <dt>Date</dt>
            <dd>{card.data.date}</dd>
          </div>
        ) : null}
        {card.data.status ? (
          <div>
            <dt>Status</dt>
            <dd>{card.data.status}</dd>
          </div>
        ) : null}
      </dl>
      {highlights.length > 0 ? (
        <ul className="card__bullet-list">
          {highlights.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : null}
      {sourceAction ? (
        <div className="card__footer">
          <ActionButton action={sourceAction} className="card__source-button" label={card.data.source_label || sourceAction.label} />
        </div>
      ) : null}
    </article>
  );
}
