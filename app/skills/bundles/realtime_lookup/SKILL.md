# Realtime Lookup Skill — Stage 3: Fairy Realtime Autonomous Lookup Engine

Fast, autonomous real-time data queries with 5-stage execution intelligence.

## Purpose

Handles queries needing immediate factual answers:
- **Weather**: Current conditions via Open-Meteo (free, no key)
- **Time**: Current time in any city via ZoneInfo IANA database
- **Crypto**: Live prices via CoinGecko
- **Stock**: Live quotes via Yahoo Finance
- **Exchange Rates**: Live FX via open.er-api.com
- **Fuel Prices**: Via search + webpage fallback
- **Sports Scores**: Via search snippets

## Architecture: 3-Layer Engine

### Layer 1 — Query Understanding
`app/realtime/query_analyzer.py`
Detects subtype and extracts entities. Multilingual (Chinese + English).
Subtypes: `weather`, `time`, `crypto`, `stock`, `exchange`, `fuel`, `sports`

### Layer 2 — Execution Intelligence (5-Stage)
`app/realtime/lookup_engine.py`
- **Stage 1**: Structured provider (Open-Meteo / CoinGecko / Yahoo / FX API)
- **Stage 2**: Search snippet extraction with numeric pattern matching
- **Stage 3**: Webpage open + DOM text parse (CRITICAL for fuel prices)
- **Stage 4**: Multi-source cross-validation + confidence scoring
- **Stage 5**: Fallback snippet (returns best available result)

### Layer 3 — Result Rendering
`app/realtime/result_builder.py`
Returns unified `RealtimeResult` with `speech_text`, `card_payload`, `numeric_value`, `confidence`.

## Structured Providers (Stage 1)

| Subtype  | Provider         | API                           | Key Required |
|----------|------------------|-------------------------------|--------------|
| weather  | Open-Meteo       | open-meteo.com                | No           |
| time     | Python ZoneInfo  | IANA tz database              | No           |
| crypto   | CoinGecko        | api.coingecko.com             | No           |
| stock    | Yahoo Finance    | query1.finance.yahoo.com      | No           |
| exchange | open.er-api.com  | open.er-api.com + frankfurter | No           |
| fuel     | None             | Stage 2/3 only                | —            |

## Card Protocol

All cards MUST follow this structure:
```json
{
  "type": "weather_card",
  "title": "Melbourne Weather",
  "primary": "21°C",
  "secondary": ["Humidity 55%", "Wind 8km/h"],
  "timestamp": "2026-03-20T14:35:00Z",
  "confidence": 0.95
}
```
Card types: `weather_card`, `time_card`, `crypto_card`, `stock_card`,
`fx_card`, `fuel_card`, `sports_card`, `generic_card`

**UI decides animation and placement. Skill must NOT manipulate UI.**

## Timezone Resolution (Critical Fix)

Timezone is ALWAYS resolved via:
1. `TimezoneResolver.resolve(location)` → IANA string
2. `ZoneInfo(iana_name)` → timezone object
3. `datetime.now(tz)` → current time

NEVER derived by UTC offset arithmetic. Supports 100+ cities with Chinese
and English names. Dynamic search fallback for unknown cities.

## Parallel Execution

For multi-intent queries (e.g. "东京现在几点天气如何"):
```python
engine = LookupEngine(search_fn=search_web, fetch_page_fn=read_webpage)
results = engine.run_parallel(["现在东京几点", "东京今天天气"])
```

## Supported Query Examples

### Weather
- "墨尔本今天天气怎么样?" → weather_card: 21°C, Partly cloudy
- "What's the weather in Melbourne?" → weather_card

### Time
- "现在东京几点?" → time_card: 2026-03-20 14:35 JST (下午)
- "纽约现在是白天还是晚上?" → time_card with is_daytime field

### Crypto
- "比特币现在价格多少?" → crypto_card: $67,450 USD
- "ETH price" → crypto_card

### Stock
- "英伟达现在股价多少?" → stock_card: NVDA $875.42
- "NVIDIA stock price" → stock_card

### Exchange Rate
- "美元兑澳元多少?" → fx_card: 1 USD = 1.5234 AUD
- "USD to AUD rate" → fx_card

### Fuel Price
- "Forest Hill 附近 98 号油价多少?" → fuel_card (Stage 3 webpage)
- "Petrol price Melbourne" → fuel_card

## Performance Targets

| Operation    | Target | Timeout |
|-------------|--------|---------|
| Time lookup  | <100ms | 1s      |
| Weather      | <2s    | 8s      |
| Crypto/FX    | <1.5s  | 8s      |
| Stock        | <2s    | 8s      |
| Fuel (web)   | <4s    | 15s     |

## Fallback Behavior

1. Stage 1 provider fails → Stage 2 snippet search
2. Stage 2 no numeric found → Stage 3 open webpage
3. Stage 3 page unreadable → Stage 4 retry with different query
4. All fail → Stage 5 best-effort snippet OR failure response

## STRICT LLM BEHAVIOR CONSTRAINTS

**These rules are absolute. No exceptions.**

- **NEVER fabricate realtime values.** If you do not have a numeric fact
  from a provider or search result, do not invent one.
- **NEVER say** "可能", "大概", "我估计", "应该是", "建议查询" for numeric facts.
- **NEVER derive time by offset math.** Always use ZoneInfo.
- **If all stages fail**, respond exactly: "实时数据暂不可用"
- **Do NOT route to shopping/travel/flight/general-research skills** for
  weather, price, time, fuel, or rate queries.
- **Do NOT use heavy web_research pipeline** for simple numeric lookups.
- **Do NOT hallucinate DST explanations** or UTC offset reasoning.
- **Confidence must reflect actual source quality:**
  - Stage 1 structured API: 0.90–0.99
  - Stage 2 snippet: 0.65–0.80
  - Stage 3 webpage: 0.70–0.85
  - Stage 5 fallback: 0.40–0.50

## Debug Telemetry Log Keys

```
realtime_stage_entered    stage={1-5} subtype={subtype}
provider_success          subtype={subtype}
snippet_extracted         subtype={subtype}
webpage_opened            url={url}
structured_value_found    stage=3 subtype={subtype}
multi_source_validated    subtype={subtype} deviation={float}
card_built                subtype={subtype} stage={stage}
```
