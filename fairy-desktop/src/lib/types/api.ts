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

export interface PersistedAttachment {
  path: string;
  name: string;
  mime_type: string;
  size_bytes: number;
}

export interface VoiceSynthesizeResponse {
  audio_base64: string;
  mime_type: string;
}

export interface BaseCardEnvelope<TType extends string, TData> {
  type: TType;
  version: string;
  data: TData;
  layout: string;
  metadata?: Record<string, unknown>;
  actions?: CardAction[];
}

export interface CardAction {
  type: "open_url" | "open_source" | "open_map" | "navigate" | string;
  label: string;
  url?: string;
  payload?: Record<string, unknown>;
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
  map_preview_url?: string;
  image_path?: string;
  external_map_url?: string;
  navigate_url?: string;
  summary?: string;
}

export interface TimeCardData {
  location?: string;
  time_text?: string;
  date_text?: string;
  weekday?: string;
  period?: string;
  timezone?: string;
  is_daytime?: boolean;
  summary?: string;
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
  actions?: CardAction[];
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
  source_url?: string;
  source_label?: string;
}

export interface VisualReadCardData {
  region?: string;
  summary?: string;
  confidence?: number;
  source_url?: string;
  visual_type?: string;
  screenshot_path?: string;
}

export interface CompareItemData {
  title: string;
  url?: string;
  summary?: string;
  highlights?: string[];
  actions?: CardAction[];
}

export interface SourceLinkData {
  title: string;
  url: string;
}

export interface SpecsCardData {
  title?: string;
  summary?: string;
  fields?: GenericInfoField[];
  source_url?: string;
  source_label?: string;
}

export interface CompareCardData {
  title?: string;
  summary?: string;
  items?: CompareItemData[];
  shared_points?: string[];
  differences?: string[];
  recommendation?: string;
  sources?: SourceLinkData[];
  source_url?: string;
  source_label?: string;
}

export interface ReleaseCardData {
  title?: string;
  summary?: string;
  date?: string;
  status?: string;
  highlights?: string[];
  source_url?: string;
  source_label?: string;
}

export interface WebBriefCardData {
  title?: string;
  summary?: string;
  bullets?: string[];
  source_url?: string;
  source_label?: string;
}

export type WeatherCardEnvelope = BaseCardEnvelope<"weather", WeatherCardData>;
export type TimeCardEnvelope = BaseCardEnvelope<"time", TimeCardData>;
export type LocationCardEnvelope = BaseCardEnvelope<"location" | "map_preview", MapPreviewCardData>;
export type NewsCardEnvelope = BaseCardEnvelope<"news_list", NewsCardData>;
export type GenericInfoCardEnvelope = BaseCardEnvelope<"generic_info", GenericInfoCardData>;
export type VisualReadCardEnvelope = BaseCardEnvelope<"visual_read", VisualReadCardData>;
export type SpecsCardEnvelope = BaseCardEnvelope<"specs", SpecsCardData>;
export type CompareCardEnvelope = BaseCardEnvelope<"compare", CompareCardData>;
export type ReleaseCardEnvelope = BaseCardEnvelope<"release", ReleaseCardData>;
export type WebBriefCardEnvelope = BaseCardEnvelope<"web_brief", WebBriefCardData>;
export type UnknownCardEnvelope = BaseCardEnvelope<string, Record<string, unknown>>;

export type CardUnion =
  | WeatherCardEnvelope
  | TimeCardEnvelope
  | LocationCardEnvelope
  | NewsCardEnvelope
  | VisualReadCardEnvelope
  | GenericInfoCardEnvelope
  | SpecsCardEnvelope
  | CompareCardEnvelope
  | ReleaseCardEnvelope
  | WebBriefCardEnvelope
  | UnknownCardEnvelope;

export interface SpeechMeta {
  mode?: string;
  text?: string;
  allow_streaming?: boolean;
}

export type FairyWorkState = "standby" | "relaxed" | "thinking" | "focused" | "uncertain" | "alert";

export interface FairyMeta {
  state?: FairyWorkState;
  certainty?: number;
  urgency?: number;
  tone?: string;
  suggested_tools?: string[];
  next_question?: string | null;
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
    fairy?: FairyMeta;
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
  meta: Record<string, unknown> & { fairy?: FairyMeta };
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
  meta: Record<string, unknown> & { fairy?: FairyMeta };
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

export type RuntimeAssistantState =
  | "booting"
  | "warming_up"
  | "idle"
  | "thinking"
  | "analyzing"
  | "replying"
  | "error"
  | "sleeping";

export interface RuntimeStateTraceEntry {
  previous_state: RuntimeAssistantState;
  current_state: RuntimeAssistantState;
  reason?: string;
  request_id?: string | null;
  session_id?: string | null;
  timestamp_ms: number;
}

export interface SystemStateResponse {
  backend_status: string;
  current_state: RuntimeAssistantState;
  fairy?: FairyMeta;
  active_session?: string | null;
  active_stream_request?: string | null;
  is_streaming: boolean;
  last_error?: string | null;
  capabilities: Record<string, unknown>;
  runtime_state_trace: RuntimeStateTraceEntry[];
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
