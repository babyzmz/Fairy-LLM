import type {
  CardUnion,
  GenericInfoCardEnvelope,
  LocationCardEnvelope,
  NewsCardEnvelope,
  WeatherCardEnvelope,
} from "../../lib/types/api";
import { GenericInfoCard } from "./cards/GenericInfoCard";
import { MapPreviewCard } from "./cards/MapPreviewCard";
import { NewsCard } from "./cards/NewsCard";
import { WeatherCard } from "./cards/WeatherCard";

interface CardRendererProps {
  card: CardUnion;
}

export const SUPPORTED_CARD_TYPES = ["weather", "location", "map_preview", "news_list", "generic_info"] as const;

export function CardRenderer({ card }: CardRendererProps): JSX.Element {
  switch (card.type) {
    case "weather":
      return <WeatherCard card={card as WeatherCardEnvelope} />;
    case "location":
    case "map_preview":
      return <MapPreviewCard card={card as LocationCardEnvelope} />;
    case "news_list":
      return <NewsCard card={card as NewsCardEnvelope} />;
    case "generic_info":
      return <GenericInfoCard card={card as GenericInfoCardEnvelope} />;
    default:
      console.warn("[FairyDesktop] unsupported card type", card.type, card);
      return (
        <article className="card card--fallback">
          <div className="card__header">
            <h3>Unsupported card: {card.type}</h3>
          </div>
          <pre>{JSON.stringify(card.data, null, 2)}</pre>
        </article>
      );
  }
}
