import type { TimeCardEnvelope } from "../../../lib/types/api";

interface TimeCardProps {
  card: TimeCardEnvelope;
}

export function TimeCard({ card }: TimeCardProps): JSX.Element {
  const data = card.data;
  const detail = [data.date_text, data.weekday, data.period].filter(Boolean).join(" ");

  return (
    <article className="card card--time">
      <div className="card__header">
        <div>
          <h3>{data.location || "Local time"}</h3>
          {detail ? <p>{detail}</p> : null}
        </div>
        {data.timezone ? <p className="time-card__timezone">{data.timezone}</p> : null}
      </div>
      <div className="time-card__clock">{data.time_text || "--:--"}</div>
      {data.summary ? <p>{data.summary}</p> : null}
    </article>
  );
}
