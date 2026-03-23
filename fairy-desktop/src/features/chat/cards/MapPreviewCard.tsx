import { AssetResolver } from "../../../lib/assets/assetResolver";
import type { LocationCardEnvelope } from "../../../lib/types/api";

interface MapPreviewCardProps {
  card: LocationCardEnvelope;
}

export function MapPreviewCard({ card }: MapPreviewCardProps): JSX.Element {
  const data = card.data;
  const previewUrl = AssetResolver.resolveMapPreview(data.map_preview_path || data.image_path);
  const title = data.title || data.city || "Location";
  const subtitle = [data.address, data.region, data.country].filter(Boolean).join(", ");

  return (
    <article className="card">
      <div className="card__header">
        <div>
          <h3>{title}</h3>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
      </div>
      {previewUrl ? <img className="map-card__preview" src={previewUrl} alt={title} /> : null}
      <dl className="card__meta-grid">
        {data.distance_text ? (
          <>
            <dt>Distance</dt>
            <dd>{data.distance_text}</dd>
          </>
        ) : null}
        {data.lat !== undefined || data.lon !== undefined ? (
          <>
            <dt>Coordinates</dt>
            <dd>
              {data.lat ?? "--"}, {data.lon ?? "--"}
            </dd>
          </>
        ) : null}
      </dl>
      <div className="card__actions">
        {data.external_map_url ? (
          <a href={data.external_map_url} target="_blank" rel="noreferrer">
            Open in Maps
          </a>
        ) : null}
        {data.navigate_url ? (
          <a href={data.navigate_url} target="_blank" rel="noreferrer">
            Navigate
          </a>
        ) : null}
      </div>
    </article>
  );
}
