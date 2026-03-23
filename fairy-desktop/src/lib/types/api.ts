export interface ApiErrorModel {
  code: string;
  message: string;
}

export interface HealthResponse {
  status: string;
  service: string;
}

export interface CapabilitiesResponse {
  chat: boolean;
  streaming: boolean;
  voice_input: boolean;
  voice_output: boolean;
  cards: string[];
  system_actions: string[];
}

export interface ChatInvokeRequest {
  message: string;
  session_id: string;
  attachments?: string[] | null;
}

export interface BaseCardEnvelope<TType extends string, TData> {
  type: TType;
  version: string;
  data: TData;
  layout: string;
  metadata?: Record<string, unknown>;
}

export interface WeatherCardData {
  city?: string;
  country?: string;
  location?: string;
  condition?: string;
  condition_key?: string;
  temperature_c?: number | string;
  temp?: number | string;
  high_c?: number | string;
  high?: number | string;
  low_c?: number | string;
  low?: number | string;
  feels_like_c?: number | string;
  feels_like?: number | string;
  humidity_percent?: number | string;
  wind_kmh?: number | string;
  wind?: number | string;
  icon_key?: string;
  icon_path?: string;
}

export interface MapPreviewCardData {
  title?: string;
  address?: string;
  city?: string;
  region?: string;
  country?: string;
  lat?: number | string;
  lon?: number | string;
  distance_text?: string;
  map_preview_path?: string;
  image_path?: string;
  external_map_url?: string;
  navigate_url?: string;
}

export interface NewsItemData {
  headline?: string;
  title?: string;
  source?: string;
  published_at?: string;
  summary?: string;
  snippet?: string;
  image_path?: string;
  url?: string;
}

export interface NewsCardData {
  title?: string;
  items: NewsItemData[];
}

export interface GenericInfoField {
  label: string;
  value: string;
}

export interface GenericInfoCardData {
  title?: string;
  summary?: string;
  fields?: GenericInfoField[];
}

export type WeatherCardEnvelope = BaseCardEnvelope<"weather", WeatherCardData>;
export type LocationCardEnvelope = BaseCardEnvelope<"location" | "map_preview", MapPreviewCardData>;
export type NewsCardEnvelope = BaseCardEnvelope<"news_list", NewsCardData>;
export type GenericInfoCardEnvelope = BaseCardEnvelope<"generic_info", GenericInfoCardData>;
export type UnknownCardEnvelope = BaseCardEnvelope<string, Record<string, unknown>>;

export type CardUnion =
  | WeatherCardEnvelope
  | LocationCardEnvelope
  | NewsCardEnvelope
  | GenericInfoCardEnvelope
  | UnknownCardEnvelope;

export interface SpeechMeta {
  mode?: string;
  text?: string;
  allow_streaming?: boolean;
}

export interface ChatInvokeResponse {
  request_id: string;
  session_id: string;
  text: string;
  cards: CardUnion[];
  meta: {
    intent?: string;
    modality?: string;
    speech?: SpeechMeta;
    progress_events?: Array<{ stage: string; text: string }>;
    [key: string]: unknown;
  };
  errors: ApiErrorModel[];
}

export type StreamEventType = "message_start" | "progress" | "text_delta" | "card" | "message_end" | "error";

export interface StreamEventBase {
  event: StreamEventType;
  request_id: string;
  session_id: string;
  sequence: number;
  timestamp_ms: number;
}

export interface MessageStartStreamEvent extends StreamEventBase {
  event: "message_start";
  meta: Record<string, unknown>;
}

export interface ProgressStreamEvent extends StreamEventBase {
  event: "progress";
  stage: string;
  text: string;
}

export interface TextDeltaStreamEvent extends StreamEventBase {
  event: "text_delta";
  text: string;
}

export interface CardStreamEvent extends StreamEventBase {
  event: "card";
  card: CardUnion;
}

export interface MessageEndStreamEvent extends StreamEventBase {
  event: "message_end";
  text: string;
  cards: CardUnion[];
  meta: Record<string, unknown>;
  errors: ApiErrorModel[];
}

export interface ErrorStreamEvent extends StreamEventBase {
  event: "error";
  code: string;
  message: string;
}

export type ChatStreamEvent =
  | MessageStartStreamEvent
  | ProgressStreamEvent
  | TextDeltaStreamEvent
  | CardStreamEvent
  | MessageEndStreamEvent
  | ErrorStreamEvent;

export interface SystemEventModel {
  event: string;
  timestamp_ms: number;
  request_id?: string | null;
  session_id?: string | null;
  detail: Record<string, unknown>;
}

export interface SystemStateResponse {
  backend_status: string;
  active_session?: string | null;
  active_stream_request?: string | null;
  is_streaming: boolean;
  last_error?: string | null;
  capabilities: Record<string, unknown>;
  recent_events: SystemEventModel[];
}

export interface DesktopBackendLifecycleEvent {
  status: string;
  url: string;
  message: string;
  pid?: number | null;
  timestamp_ms: number;
}

export interface DesktopSystemState {
  bridge_status: string;
  backend_url: string;
  bridge_message: string;
  pid?: number | null;
  bridge_events: DesktopBackendLifecycleEvent[];
  runtime_state: SystemStateResponse | null;
}

export interface SystemActionResponse {
  action: string;
  ok: boolean;
  message: string;
  detail: Record<string, unknown>;
  system_state?: DesktopSystemState;
}
