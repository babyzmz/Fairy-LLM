# Implementation Roadmap: Realtime Lookup Execution Engine

## Overview

This document outlines the implementation plan for Stage 3: Realtime Execution Engine.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│              RealtimeSkillExecutor                       │
│  (app/skills/realtime_executor.py)                      │
│                                                          │
│  - Intent classification                                │
│  - Tool selection                                       │
│  - Execution with timeout                               │
│  - Error handling & fallback                            │
└──────────────────────────────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
   ┌─────────────┐ ┌──────────────┐ ┌──────────────┐
   │ Weather     │ │ Exchange     │ │ Stock        │
   │ Tool        │ │ Rate Tool    │ │ Quote Tool   │
   │             │ │              │ │              │
   │ - Location  │ │ - Pair       │ │ - Ticker     │
   │ - Forecast  │ │ - Rate       │ │ - Price      │
   │ - API call  │ │ - API call   │ │ - API call   │
   └─────────────┘ └──────────────┘ └──────────────┘
        │                │                │
        ▼                ▼                ▼
   ┌─────────────┐ ┌──────────────┐ ┌──────────────┐
   │ Open-Meteo  │ │ Exchange API │ │ Stock API    │
   │ (Free)      │ │ (Free)       │ │ (Free)       │
   └─────────────┘ └──────────────┘ └──────────────┘
```

## Implementation Plan

### Phase 1: Core Executor (Week 1)

**File**: `app/skills/realtime_executor.py`

```python
class RealtimeSkillExecutor:
    """Execute realtime lookup queries with minimal overhead."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.timeout = config.get("realtime_timeout_sec", 8)

    async def execute(
        self,
        intent_type: str,
        query: str,
        context: dict[str, Any] | None = None,
    ) -> SkillResult:
        """Execute realtime lookup."""
        # Route to specific handler
        # Execute with timeout
        # Handle errors
        # Return result
```

**Tasks**:
- [ ] Create base executor class
- [ ] Add intent routing
- [ ] Add timeout handling
- [ ] Add error handling
- [ ] Add logging

### Phase 2: Weather Tool (Week 1)

**File**: `app/skills/tools/weather_tool.py`

```python
class WeatherTool:
    """Get weather forecast using Open-Meteo API."""

    async def get_weather(
        self,
        location: str,
        day_offset: int = 0,
    ) -> dict[str, Any]:
        """Get weather for location."""
        # Geocode location
        # Call Open-Meteo API
        # Parse response
        # Return formatted result
```

**APIs**:
- Geocoding: `https://geocoding-api.open-meteo.com/v1/search`
- Forecast: `https://api.open-meteo.com/v1/forecast`
- IP Geolocation: `https://ipwho.is/`

**Tasks**:
- [ ] Implement location extraction
- [ ] Implement geocoding
- [ ] Implement forecast API call
- [ ] Add error handling
- [ ] Add caching (optional)

### Phase 3: Exchange Rate Tool (Week 1)

**File**: `app/skills/tools/exchange_tool.py`

```python
class ExchangeRateTool:
    """Get exchange rates."""

    async def get_exchange_rate(
        self,
        from_currency: str,
        to_currency: str,
    ) -> dict[str, Any]:
        """Get exchange rate."""
        # Validate currencies
        # Call exchange API
        # Parse response
        # Return formatted result
```

**APIs**:
- Open Exchange Rates: `https://openexchangerates.org/api/latest`
- Fixer.io: `https://api.fixer.io/latest`
- Free Forex API: `https://api.exchangerate-api.com/v4/latest/`

**Tasks**:
- [ ] Implement currency pair extraction
- [ ] Implement API call
- [ ] Add error handling
- [ ] Add caching

### Phase 4: Stock Quote Tool (Week 2)

**File**: `app/skills/tools/stock_tool.py`

```python
class StockQuoteTool:
    """Get stock quotes."""

    async def get_stock_quote(
        self,
        ticker: str,
    ) -> dict[str, Any]:
        """Get stock quote."""
        # Validate ticker
        # Call stock API
        # Parse response
        # Return formatted result
```

**APIs**:
- Alpha Vantage: `https://www.alphavantage.co/query`
- IEX Cloud: `https://cloud.iexapis.com/stable/stock/`
- Finnhub: `https://finnhub.io/api/v1/quote`

**Tasks**:
- [ ] Implement ticker extraction
- [ ] Implement API call
- [ ] Add error handling
- [ ] Add caching

### Phase 5: Crypto Quote Tool (Week 2)

**File**: `app/skills/tools/crypto_tool.py`

```python
class CryptoQuoteTool:
    """Get cryptocurrency quotes."""

    async def get_crypto_quote(
        self,
        symbol: str,
        currency: str = "USD",
    ) -> dict[str, Any]:
        """Get crypto quote."""
        # Validate symbol
        # Call crypto API
        # Parse response
        # Return formatted result
```

**APIs**:
- CoinGecko: `https://api.coingecko.com/api/v3/simple/price`
- CoinMarketCap: `https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest`
- Kraken: `https://api.kraken.com/0/public/Ticker`

**Tasks**:
- [ ] Implement symbol extraction
- [ ] Implement API call
- [ ] Add error handling
- [ ] Add caching

### Phase 6: Sports Score Tool (Week 2)

**File**: `app/skills/tools/sports_tool.py`

```python
class SportsTool:
    """Get sports scores."""

    async def get_sports_score(
        self,
        event: str,
    ) -> dict[str, Any]:
        """Get sports score."""
        # Parse event
        # Call sports API
        # Parse response
        # Return formatted result
```

**APIs**:
- ESPN API: `https://site.api.espn.com/`
- TheSportsDB: `https://www.thesportsdb.com/api/v1/json/`
- Rapid API Sports: Multiple sports APIs

**Tasks**:
- [ ] Implement event extraction
- [ ] Implement API call
- [ ] Add error handling
- [ ] Add caching

### Phase 7: Fuel Price Tool (Week 3)

**File**: `app/skills/tools/fuel_tool.py`

```python
class FuelPriceTool:
    """Get fuel prices."""

    async def get_fuel_price(
        self,
        location: str,
        fuel_type: str = "petrol",
    ) -> dict[str, Any]:
        """Get fuel price."""
        # Parse location
        # Call fuel API
        # Parse response
        # Return formatted result
```

**APIs**:
- Global Petrol Prices: `https://www.globalpetrolprices.com/`
- Fuel Price API: Various regional APIs
- Web scraping: Last resort

**Tasks**:
- [ ] Implement location extraction
- [ ] Implement API call
- [ ] Add error handling
- [ ] Add caching

### Phase 8: Integration & Testing (Week 3)

**Files**:
- `tests/test_realtime_executor.py`
- `tests/test_weather_tool.py`
- `tests/test_exchange_tool.py`
- `tests/test_stock_tool.py`
- `tests/test_crypto_tool.py`
- `tests/test_sports_tool.py`
- `tests/test_fuel_tool.py`

**Tasks**:
- [ ] Unit tests for each tool
- [ ] Integration tests with executor
- [ ] Mock API responses
- [ ] Test error handling
- [ ] Test timeout behavior
- [ ] Test fallback logic

## API Selection Criteria

1. **Free Tier**: No API key required or generous free tier
2. **Reliability**: High uptime, good documentation
3. **Speed**: Fast response times (<2s)
4. **Coverage**: Global coverage where applicable
5. **Rate Limits**: Reasonable rate limits for our use case

## Error Handling Strategy

```python
class RealtimeError(Exception):
    """Base realtime lookup error."""
    pass

class LocationNotFoundError(RealtimeError):
    """Location not found."""
    pass

class APIError(RealtimeError):
    """External API error."""
    pass

class TimeoutError(RealtimeError):
    """Operation timed out."""
    pass

class MissingParameterError(RealtimeError):
    """Required parameter missing."""
    pass
```

## Fallback Strategy

1. **Try Real-Time API**: Call primary API
2. **On Timeout**: Try search snippet fallback
3. **On API Error**: Try alternative API
4. **On All Failures**: Ask for clarification or suggest web_research

## Caching Strategy

```python
class RealtimeCache:
    """Simple in-memory cache for realtime data."""

    def __init__(self, ttl_sec: int = 300):
        self.ttl_sec = ttl_sec
        self.cache: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        """Get cached value if not expired."""

    def set(self, key: str, value: Any) -> None:
        """Set cached value."""
```

**Cache Keys**:
- Weather: `weather:{location}:{day_offset}`
- Exchange: `exchange:{from}:{to}`
- Stock: `stock:{ticker}`
- Crypto: `crypto:{symbol}:{currency}`
- Sports: `sports:{event}`
- Fuel: `fuel:{location}:{type}`

## Performance Targets

| Operation | Target | Timeout |
|-----------|--------|---------|
| Weather lookup | <2s | 5s |
| Exchange rate | <1s | 3s |
| Stock quote | <1.5s | 4s |
| Crypto quote | <1.5s | 4s |
| Sports score | <2s | 5s |
| Fuel price | <2s | 5s |

## Monitoring & Metrics

```python
class RealtimeMetrics:
    """Track realtime lookup metrics."""

    def record_execution(
        self,
        intent_type: str,
        duration_ms: float,
        success: bool,
        fallback_used: bool,
    ) -> None:
        """Record execution metrics."""
```

**Metrics to Track**:
- Success rate by intent type
- Average response time
- Fallback rate
- API error rate
- Timeout rate

## Deployment Checklist

- [ ] All tools implemented and tested
- [ ] Integration tests passing
- [ ] Performance targets met
- [ ] Error handling comprehensive
- [ ] Logging in place
- [ ] Metrics collection working
- [ ] Documentation complete
- [ ] Code review approved
- [ ] Staging deployment successful
- [ ] Production deployment

## Timeline

- **Week 1**: Core executor + Weather + Exchange tools
- **Week 2**: Stock + Crypto + Sports tools
- **Week 3**: Fuel tool + Integration + Testing
- **Week 4**: Monitoring + Optimization + Deployment

## Success Criteria

1. ✅ All realtime intents route correctly
2. ✅ Response time < 3s for 95% of queries
3. ✅ Success rate > 90%
4. ✅ Fallback works when APIs fail
5. ✅ No regressions in web_research
6. ✅ Comprehensive test coverage (>90%)
7. ✅ Monitoring and metrics in place
