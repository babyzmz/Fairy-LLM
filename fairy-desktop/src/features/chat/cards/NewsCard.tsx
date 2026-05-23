import { AssetResolver } from "../../../lib/assets/assetResolver";
import { firstCardAction } from "../../../lib/actions/cardActions";
import type { NewsCardEnvelope } from "../../../lib/types/api";
import { ActionButton } from "./ActionButton";

interface NewsCardProps {
  card: NewsCardEnvelope;
}

export function NewsCard({ card }: NewsCardProps): JSX.Element {
  const items = card.data.items ?? [];
  return (
    <article className="card">
      <div className="card__header">
        <h3>{card.data.title || "News"}</h3>
      </div>
      <div className="news-card__list">
        {items.map((item, index) => {
          const title = item.headline || item.title || `Item ${index + 1}`;
          const imageUrl = AssetResolver.resolveNewsThumbnail(item.image_path);
          return (
            <section className="news-card__item" key={`${title}-${index}`}>
              {imageUrl ? <img className="news-card__thumb" src={imageUrl} alt="" /> : null}
              <div className="news-card__content">
                <h4>{title}</h4>
                <p>{item.summary || item.snippet || ""}</p>
                <div className="news-card__meta">
                  <span>{item.source || "Source"}</span>
                  {item.published_at ? <span>{item.published_at}</span> : null}
                </div>
                <ActionButton action={firstCardAction(item.actions, ["open_url"])} className="card__inline-action" label="Open" />
              </div>
            </section>
          );
        })}
      </div>
    </article>
  );
}
