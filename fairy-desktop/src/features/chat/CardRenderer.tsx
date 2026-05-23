import type {
  CardUnion,
  CompareCardEnvelope,
  GenericInfoCardEnvelope,
  LocationCardEnvelope,
  NewsCardEnvelope,
  ReleaseCardEnvelope,
  SpecsCardEnvelope,
  TimeCardEnvelope,
  UnknownCardEnvelope,
  VisualReadCardEnvelope,
  WeatherCardEnvelope,
  WebBriefCardEnvelope,
} from "../../lib/types/api";
import { CompareCard } from "./cards/CompareCard";
import { FallbackCard } from "./cards/FallbackCard";
import { GenericInfoCard } from "./cards/GenericInfoCard";
import { MapPreviewCard } from "./cards/MapPreviewCard";
import { NewsCard } from "./cards/NewsCard";
import { ReleaseCard } from "./cards/ReleaseCard";
import { SpecsCard } from "./cards/SpecsCard";
import { TimeCard } from "./cards/TimeCard";
import { VisualReadCard } from "./cards/VisualReadCard";
import { WeatherCard } from "./cards/WeatherCard";
import { WebBriefCard } from "./cards/WebBriefCard";

interface CardRendererProps {
  card: CardUnion;
}

type CardRendererComponent = (props: { card: CardUnion }) => JSX.Element;

export const SUPPORTED_CARD_TYPES = [
  "weather",
  "time",
  "location",
  "map_preview",
  "news_list",
  "visual_read",
  "generic_info",
  "specs",
  "compare",
  "release",
  "web_brief",
] as const;

const CARD_RENDERERS: Record<string, CardRendererComponent> = {
  weather: ({ card }) => <WeatherCard card={card as WeatherCardEnvelope} />,
  time: ({ card }) => <TimeCard card={card as TimeCardEnvelope} />,
  location: ({ card }) => <MapPreviewCard card={card as LocationCardEnvelope} />,
  map_preview: ({ card }) => <MapPreviewCard card={card as LocationCardEnvelope} />,
  news_list: ({ card }) => <NewsCard card={card as NewsCardEnvelope} />,
  visual_read: ({ card }) => <VisualReadCard card={card as VisualReadCardEnvelope} />,
  generic_info: ({ card }) => <GenericInfoCard card={card as GenericInfoCardEnvelope} />,
  specs: ({ card }) => <SpecsCard card={card as SpecsCardEnvelope} />,
  compare: ({ card }) => <CompareCard card={card as CompareCardEnvelope} />,
  release: ({ card }) => <ReleaseCard card={card as ReleaseCardEnvelope} />,
  web_brief: ({ card }) => <WebBriefCard card={card as WebBriefCardEnvelope} />,
};

export function CardRenderer({ card }: CardRendererProps): JSX.Element {
  const renderer = CARD_RENDERERS[card.type];
  if (!renderer) {
    console.warn("[FairyDesktop] unsupported card type", card.type, card);
    return <FallbackCard card={card as UnknownCardEnvelope} />;
  }
  return renderer({ card });
}
