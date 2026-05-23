import { firstCardAction } from "../../../lib/actions/cardActions";
import type { WebBriefCardEnvelope } from "../../../lib/types/api";
import { ActionButton } from "./ActionButton";

interface WebBriefCardProps {
  card: WebBriefCardEnvelope;
}

export function WebBriefCard({ card }: WebBriefCardProps): JSX.Element {
  const bullets = card.data.bullets ?? [];
  const sourceAction = firstCardAction(card.actions, ["open_source", "open_url"]);

  return (
    <article className="card card--web-brief">
      <div className="card__header">
        <div>
          <h3>{card.data.title || "Web Brief"}</h3>
          {card.data.summary ? <p>{card.data.summary}</p> : null}
        </div>
      </div>
      {bullets.length > 0 ? (
        <ul className="card__bullet-list">
          {bullets.map((item) => (
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
