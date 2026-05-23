import { firstCardAction } from "../../../lib/actions/cardActions";
import type { VisualReadCardEnvelope } from "../../../lib/types/api";
import { ActionButton } from "./ActionButton";

interface VisualReadCardProps {
  card: VisualReadCardEnvelope;
}

function regionLabel(region?: string): string {
  const normalized = String(region || "").trim().toLowerCase();
  if (normalized === "top_banner") {
    return "\u9875\u9762\u9876\u90e8\u516c\u544a";
  }
  if (normalized === "hero_section") {
    return "\u9996\u5c4f\u4e3b\u533a\u57df";
  }
  if (normalized === "results_panel") {
    return "\u7ed3\u679c\u533a\u57df";
  }
  return "\u9875\u9762\u89c6\u89c9\u9605\u8bfb";
}

export function VisualReadCard({ card }: VisualReadCardProps): JSX.Element {
  const data = card.data;
  const confidence =
    typeof data.confidence === "number" && Number.isFinite(data.confidence)
      ? `${Math.round(data.confidence * 100)}%`
      : "";
  const sourceAction = firstCardAction(card.actions, ["open_source", "open_url"]);

  return (
    <article className="card card--visual-read">
      <div className="card__header">
        <div>
          <h3>{regionLabel(data.region)}</h3>
          {data.visual_type ? <p>{data.visual_type}</p> : null}
        </div>
        {confidence ? <p className="visual-read-card__confidence">{confidence}</p> : null}
      </div>
      <p className="visual-read-card__summary">{data.summary || ""}</p>
      {sourceAction ? (
        <div className="visual-read-card__meta">
          <span>\u6765\u6e90</span>
          <ActionButton action={sourceAction} className="card__source-button" label={sourceAction.label} />
        </div>
      ) : null}
    </article>
  );
}
