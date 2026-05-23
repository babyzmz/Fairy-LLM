import type { UnknownCardEnvelope } from "../../../lib/types/api";

interface FallbackCardProps {
  card: UnknownCardEnvelope;
}

export function FallbackCard({ card }: FallbackCardProps): JSX.Element {
  return (
    <article className="card card--fallback">
      <div className="card__header">
        <h3>Unsupported card: {card.type}</h3>
      </div>
      <pre>{JSON.stringify(card.data, null, 2)}</pre>
    </article>
  );
}
