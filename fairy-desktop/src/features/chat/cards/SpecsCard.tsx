import { firstCardAction } from "../../../lib/actions/cardActions";
import type { SpecsCardEnvelope } from "../../../lib/types/api";
import { ActionButton } from "./ActionButton";

interface SpecsCardProps {
  card: SpecsCardEnvelope;
}

export function SpecsCard({ card }: SpecsCardProps): JSX.Element {
  const fields = card.data.fields ?? [];
  const sourceAction = firstCardAction(card.actions, ["open_source", "open_url"]);

  return (
    <article className="card card--specs">
      <div className="card__header">
        <div>
          <h3>{card.data.title || "Tech Specs"}</h3>
          {card.data.summary ? <p>{card.data.summary}</p> : null}
        </div>
      </div>
      {fields.length > 0 ? (
        <dl className="card__meta-grid">
          {fields.map((field) => (
            <div key={`${field.label}-${field.value}`}>
              <dt>{field.label}</dt>
              <dd>{field.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {sourceAction ? (
        <div className="card__footer">
          <ActionButton action={sourceAction} className="card__source-button" label={card.data.source_label || sourceAction.label} />
        </div>
      ) : null}
    </article>
  );
}
