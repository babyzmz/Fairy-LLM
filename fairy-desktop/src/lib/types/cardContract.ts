import type {
  ApiErrorModel,
  CardAction,
  CardUnion,
  ChatInvokeResponse,
  ChatStreamEvent,
  CompareCardData,
  CompareItemData,
  GenericInfoCardData,
  GenericInfoField,
  MapPreviewCardData,
  NewsCardData,
  NewsItemData,
  ReleaseCardData,
  SourceLinkData,
  SpecsCardData,
  VisualReadCardData,
  WeatherCardData,
  WebBriefCardData,
  TimeCardData,
} from "./api";

const KNOWN_CARD_TYPES = new Set([
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
]);

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function asNumberLike(value: unknown): number | string | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const text = value.trim();
    return text ? text : undefined;
  }
  return undefined;
}

function asBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function normalizeStringList(value: unknown, limit = 8): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => asString(item))
    .filter((item) => item.length > 0)
    .slice(0, limit);
}

function normalizeActions(value: unknown): CardAction[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => {
      const record = asRecord(item);
      const label = asString(record.label) || "Open";
      const type = asString(record.type) || "open_url";
      const url = asString(record.url) || undefined;
      const payload = asRecord(record.payload);
      return { type, label, url, payload };
    })
    .filter((item) => item.type.length > 0);
}

function normalizeFields(value: unknown): GenericInfoField[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => {
      const record = asRecord(item);
      const label = asString(record.label) || asString(record.name);
      const fieldValue = asString(record.value);
      if (!label || !fieldValue) {
        return null;
      }
      return { label, value: fieldValue } satisfies GenericInfoField;
    })
    .filter((item): item is GenericInfoField => Boolean(item));
}

function normalizeSources(value: unknown): SourceLinkData[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => {
      const record = asRecord(item);
      const url = asString(record.url);
      if (!url) {
        return null;
      }
      return {
        title: asString(record.title) || url,
        url,
      } satisfies SourceLinkData;
    })
    .filter((item): item is SourceLinkData => Boolean(item));
}

function normalizeNewsItems(value: unknown): NewsItemData[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const items: NewsItemData[] = [];
  for (const item of value) {
    const record = asRecord(item);
    const title = asString(record.headline) || asString(record.title);
    if (!title) {
      continue;
    }
    const url = asString(record.url) || undefined;
    const actions = normalizeActions(record.actions);
    items.push({
      headline: asString(record.headline) || undefined,
      title: asString(record.title) || undefined,
      source: asString(record.source) || undefined,
      published_at: asString(record.published_at) || undefined,
      summary: asString(record.summary) || undefined,
      snippet: asString(record.snippet) || undefined,
      image_path: asString(record.image_path) || undefined,
      url,
      actions: actions.length > 0 ? actions : url ? [{ type: "open_url", label: "Open", url, payload: {} }] : [],
    });
  }
  return items;
}

function normalizeCompareItems(value: unknown): CompareItemData[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const items: CompareItemData[] = [];
  for (const item of value) {
    const record = asRecord(item);
    const title = asString(record.title);
    if (!title) {
      continue;
    }
    const url = asString(record.url) || undefined;
    const actions = normalizeActions(record.actions);
    items.push({
      title,
      url,
      summary: asString(record.summary) || undefined,
      highlights: normalizeStringList(record.highlights, 6),
      actions: actions.length > 0 ? actions : url ? [{ type: "open_url", label: "Open", url, payload: {} }] : [],
    });
  }
  return items;
}

function canonicalCardType(value: unknown): string {
  const raw = asString(value).toLowerCase();
  if (raw === "map_preview") {
    return "location";
  }
  return raw || "generic_info";
}

function normalizeWeatherData(raw: Record<string, unknown>): WeatherCardData {
  return {
    city: asString(raw.city) || undefined,
    country: asString(raw.country) || undefined,
    location: asString(raw.location) || undefined,
    condition: asString(raw.condition) || undefined,
    condition_key: asString(raw.condition_key) || undefined,
    temperature_c: asNumberLike(raw.temperature_c ?? raw.temp),
    temp: asNumberLike(raw.temp),
    high_c: asNumberLike(raw.high_c ?? raw.high),
    high: asNumberLike(raw.high),
    low_c: asNumberLike(raw.low_c ?? raw.low),
    low: asNumberLike(raw.low),
    feels_like_c: asNumberLike(raw.feels_like_c ?? raw.feels_like),
    feels_like: asNumberLike(raw.feels_like),
    humidity_percent: asNumberLike(raw.humidity_percent),
    wind_kmh: asNumberLike(raw.wind_kmh),
    wind: asNumberLike(raw.wind),
    icon_key: asString(raw.icon_key) || undefined,
    icon_path: asString(raw.icon_path) || undefined,
  };
}

function normalizeTimeData(raw: Record<string, unknown>): TimeCardData {
  return {
    location: asString(raw.location) || undefined,
    time_text: asString(raw.time_text) || undefined,
    date_text: asString(raw.date_text) || undefined,
    weekday: asString(raw.weekday) || undefined,
    period: asString(raw.period) || undefined,
    timezone: asString(raw.timezone) || undefined,
    is_daytime: asBoolean(raw.is_daytime),
    summary: asString(raw.summary) || undefined,
  };
}

function normalizeLocationData(raw: Record<string, unknown>): MapPreviewCardData {
  return {
    title: asString(raw.title) || undefined,
    address: asString(raw.address) || undefined,
    city: asString(raw.city) || undefined,
    region: asString(raw.region) || undefined,
    country: asString(raw.country) || undefined,
    lat: asNumberLike(raw.lat),
    lon: asNumberLike(raw.lon),
    distance_text: asString(raw.distance_text) || undefined,
    map_preview_path: asString(raw.map_preview_path) || undefined,
    map_preview_url: asString(raw.map_preview_url) || undefined,
    image_path: asString(raw.image_path) || undefined,
    external_map_url: asString(raw.external_map_url) || undefined,
    navigate_url: asString(raw.navigate_url) || undefined,
    summary: asString(raw.summary) || undefined,
  };
}

function normalizeNewsData(raw: Record<string, unknown>): NewsCardData {
  return {
    title: asString(raw.title) || "News",
    items: normalizeNewsItems(raw.items),
  };
}

function normalizeGenericInfoData(raw: Record<string, unknown>): GenericInfoCardData {
  return {
    title: asString(raw.title) || "Structured Info",
    summary: asString(raw.summary) || undefined,
    fields: normalizeFields(raw.fields),
    source_url: asString(raw.source_url) || undefined,
    source_label: asString(raw.source_label) || undefined,
  };
}

function normalizeVisualReadData(raw: Record<string, unknown>): VisualReadCardData {
  return {
    region: asString(raw.region) || undefined,
    summary: asString(raw.summary) || undefined,
    confidence: typeof raw.confidence === "number" && Number.isFinite(raw.confidence) ? raw.confidence : undefined,
    source_url: asString(raw.source_url) || undefined,
    visual_type: asString(raw.visual_type) || undefined,
    screenshot_path: asString(raw.screenshot_path) || undefined,
  };
}

function normalizeSpecsData(raw: Record<string, unknown>): SpecsCardData {
  return {
    title: asString(raw.title) || "Tech Specs",
    summary: asString(raw.summary) || undefined,
    fields: normalizeFields(raw.fields),
    source_url: asString(raw.source_url) || undefined,
    source_label: asString(raw.source_label) || undefined,
  };
}

function normalizeCompareData(raw: Record<string, unknown>): CompareCardData {
  return {
    title: asString(raw.title) || "Comparison",
    summary: asString(raw.summary) || undefined,
    items: normalizeCompareItems(raw.items),
    shared_points: normalizeStringList(raw.shared_points, 8),
    differences: normalizeStringList(raw.differences, 8),
    recommendation: asString(raw.recommendation) || undefined,
    sources: normalizeSources(raw.sources),
    source_url: asString(raw.source_url) || undefined,
    source_label: asString(raw.source_label) || undefined,
  };
}

function normalizeReleaseData(raw: Record<string, unknown>): ReleaseCardData {
  return {
    title: asString(raw.title) || "Release Update",
    summary: asString(raw.summary) || undefined,
    date: asString(raw.date) || undefined,
    status: asString(raw.status) || undefined,
    highlights: normalizeStringList(raw.highlights, 8),
    source_url: asString(raw.source_url) || undefined,
    source_label: asString(raw.source_label) || undefined,
  };
}

function normalizeWebBriefData(raw: Record<string, unknown>): WebBriefCardData {
  return {
    title: asString(raw.title) || "Web Brief",
    summary: asString(raw.summary) || undefined,
    bullets: normalizeStringList(raw.bullets, 8),
    source_url: asString(raw.source_url) || undefined,
    source_label: asString(raw.source_label) || undefined,
  };
}

function normalizeCardData(cardType: string, raw: Record<string, unknown>): unknown {
  switch (cardType) {
    case "weather":
      return normalizeWeatherData(raw);
    case "time":
      return normalizeTimeData(raw);
    case "location":
      return normalizeLocationData(raw);
    case "news_list":
      return normalizeNewsData(raw);
    case "visual_read":
      return normalizeVisualReadData(raw);
    case "specs":
      return normalizeSpecsData(raw);
    case "compare":
      return normalizeCompareData(raw);
    case "release":
      return normalizeReleaseData(raw);
    case "web_brief":
      return normalizeWebBriefData(raw);
    case "generic_info":
      return normalizeGenericInfoData(raw);
    default:
      return normalizeGenericInfoData(raw);
  }
}

function fallbackGenericData(raw: Record<string, unknown>): GenericInfoCardData {
  return {
    title: asString(raw.title) || "Unsupported card",
    summary: asString(raw.summary) || JSON.stringify(raw, null, 2),
    fields: [],
  };
}

export function normalizeCardEnvelope(input: unknown): CardUnion {
  const record = asRecord(input);
  const requestedType = asString(record.type);
  const cardType = canonicalCardType(record.type);
  const safeType = KNOWN_CARD_TYPES.has(cardType) ? cardType : "generic_info";
  const dataRecord = asRecord(record.data);
  const metadata = asRecord(record.metadata);
  let actions = normalizeActions(record.actions);
  const normalizedData = KNOWN_CARD_TYPES.has(cardType) ? normalizeCardData(cardType, dataRecord) : fallbackGenericData(dataRecord);
  const normalizedDataRecord = asRecord(normalizedData);
  if (actions.length === 0) {
    const sourceUrl = asString(normalizedDataRecord.source_url);
    const sourceLabel = asString(normalizedDataRecord.source_label) || "Source";
    const mapUrl = asString(normalizedDataRecord.external_map_url);
    const navigateUrl = asString(normalizedDataRecord.navigate_url);
    if (cardType === "location") {
      if (mapUrl) {
        actions = [...actions, { type: "open_map", label: "Open in Maps", url: mapUrl, payload: {} }];
      }
      if (navigateUrl) {
        actions = [...actions, { type: "navigate", label: "Navigate", url: navigateUrl, payload: {} }];
      }
    } else if (sourceUrl) {
      actions = [{ type: "open_source", label: sourceLabel || "Source", url: sourceUrl, payload: {} }];
    }
  }
  return {
    type: safeType,
    version: asString(record.version) || "1",
    layout: asString(record.layout) || "single",
    metadata: {
      ...metadata,
      requested_type: requestedType || undefined,
    },
    actions,
    data: normalizedData,
  } as CardUnion;
}

export function normalizeCardList(input: unknown): CardUnion[] {
  if (!Array.isArray(input)) {
    return [];
  }
  return input.map((item) => normalizeCardEnvelope(item));
}

function normalizeErrors(input: unknown): ApiErrorModel[] {
  if (!Array.isArray(input)) {
    return [];
  }
  return input
    .map((item) => {
      const record = asRecord(item);
      const message = asString(record.message);
      if (!message) {
        return null;
      }
      return {
        code: asString(record.code) || "runtime_error",
        message,
      } satisfies ApiErrorModel;
    })
    .filter((item): item is ApiErrorModel => Boolean(item));
}

export function normalizeChatInvokeResponse(input: unknown): ChatInvokeResponse {
  const record = asRecord(input);
  return {
    request_id: asString(record.request_id),
    session_id: asString(record.session_id) || "default",
    text: asString(record.text),
    cards: normalizeCardList(record.cards),
    meta: asRecord(record.meta),
    errors: normalizeErrors(record.errors),
  };
}

function normalizeStreamBase(input: Record<string, unknown>, eventName: string) {
  return {
    event: (asString(input.event) || eventName) as ChatStreamEvent["event"],
    request_id: asString(input.request_id),
    session_id: asString(input.session_id) || "default",
    sequence: typeof input.sequence === "number" && Number.isFinite(input.sequence) ? input.sequence : 0,
    timestamp_ms: typeof input.timestamp_ms === "number" && Number.isFinite(input.timestamp_ms) ? input.timestamp_ms : Date.now(),
  };
}

export function normalizeChatStreamEvent(eventName: string, input: unknown): ChatStreamEvent {
  const record = asRecord(input);
  const base = normalizeStreamBase(record, eventName);
  switch (base.event) {
    case "message_start":
      return {
        ...base,
        event: "message_start",
        meta: asRecord(record.meta),
      };
    case "progress":
      return {
        ...base,
        event: "progress",
        stage: asString(record.stage) || "progress",
        text: asString(record.text),
      };
    case "text_delta":
      return {
        ...base,
        event: "text_delta",
        text: asString(record.text),
      };
    case "card":
      return {
        ...base,
        event: "card",
        card: normalizeCardEnvelope(record.card),
      };
    case "message_end":
      return {
        ...base,
        event: "message_end",
        text: asString(record.text),
        cards: normalizeCardList(record.cards),
        meta: asRecord(record.meta),
        errors: normalizeErrors(record.errors),
      };
    case "error":
      return {
        ...base,
        event: "error",
        code: asString(record.code) || "runtime_stream_error",
        message: asString(record.message) || "Streaming request failed.",
      };
    default:
      return {
        ...base,
        event: "error",
        code: "invalid_stream_event",
        message: `Unsupported stream event: ${base.event}`,
      };
  }
}
