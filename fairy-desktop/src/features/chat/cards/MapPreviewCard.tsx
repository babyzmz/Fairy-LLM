import { AssetResolver } from "../../../lib/assets/assetResolver";
import { firstCardAction } from "../../../lib/actions/cardActions";
import type { LocationCardEnvelope } from "../../../lib/types/api";
import { ActionButton } from "./ActionButton";

interface MapPreviewCardProps {
  card: LocationCardEnvelope;
}

export function MapPreviewCard({ card }: MapPreviewCardProps): JSX.Element {
  const data = card.data;
  const previewUrl = AssetResolver.resolveMapPreview(data.map_preview_path || data.image_path);
  const title = data.title || data.city || "Location";
  const subtitle = [data.address, data.region, data.country].filter(Boolean).join(", ");
  const mapAction = firstCardAction(card.actions, ["open_map"]);
  const navigateAction = firstCardAction(card.actions, ["navigate"]);

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
        <ActionButton action={mapAction} label="Open in Maps" />
        <ActionButton action={navigateAction} label="Navigate" />
      </div>
    </article>
  );
}
