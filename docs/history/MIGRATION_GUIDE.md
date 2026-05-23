# Lazy Skill Runtime Migration Guide

## Overview

This document describes the migration from a mixed legacy/lazy skill system to a clean, isolated architecture with smart routing.

## Migration Stages

### Stage 1: ✅ New Lazy Runtime Integration (Completed)
- Router + LazyLoader + ToolExposureBroker working
- Skills structured as Anthropic-style bundles
- Legacy skills still present but not isolated

### Stage 2: ✅ Legacy Isolation + Realtime Bundle (Current)
- Legacy skills wrapped in feature-flagged isolation layer
- New `realtime_lookup` bundle for fast queries
- Smart router with intent detection
- Configuration updated with feature flags

### Stage 3: Realtime Execution Engine (Next)
- Implement realtime_lookup execution
- Add tool implementations (weather, rates, quotes, etc.)
- Integration with external APIs
- Comprehensive error handling

### Stage 4: Testing & Monitoring (Next)
- Full test coverage
- Routing decision logging
- Fallback rate monitoring
- Performance metrics

### Stage 5: Legacy Deprecation (Future)
- Gradual migration of legacy skill users
- Deprecation warnings
- Final removal timeline

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    User Query                               │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────┐
        │   LazySkillRouter              │
        │  (lazy_router.py)              │
        │                                │
        │  - RealtimeQueryDetector       │
        │  - Intent classification       │
        │  - Skill bundle selection      │
        └────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
   ┌─────────────┐ ┌──────────────┐ ┌──────────────┐
   │ realtime_   │ │ web_research │ │ news_        │
   │ lookup      │ │              │ │ intelligence │
   │ (fast)      │ │ (full)       │ │ (news)       │
   └─────────────┘ └──────────────┘ └──────────────┘
        │                │                │
        ▼                ▼                ▼
   ┌─────────────┐ ┌──────────────┐ ┌──────────────┐
   │ Minimal     │ │ Full         │ │ News         │
   │ Tools       │ │ Tools        │ │ Tools        │
   │             │ │              │ │              │
   │ - search    │ │ - search     │ │ - search     │
   │ - weather   │ │ - open_url   │ │ - open_url   │
   │ - rates     │ │ - extract    │ │ - extract    │
   │ - quotes    │ │ - snapshot   │ │ - snapshot   │
   └─────────────┘ └──────────────┘ └──────────────┘
```

## Configuration

### lazy_skills_config.json

```json
{
  "use_lazy_skills": true,
  "use_legacy_fallback": false,
  "enable_debug_logging": true,
  "graceful_fallback": true,
  "prefer_realtime": true,
  "realtime_timeout_sec": 8,
  "web_research_timeout_sec": 15
}
```

**Key Settings:**

- `use_lazy_skills`: Enable new lazy runtime (always true)
- `use_legacy_fallback`: DEPRECATED - legacy skills are now isolated
- `prefer_realtime`: Route real-time queries to realtime_lookup first
- `realtime_timeout_sec`: Timeout for fast lookups
- `web_research_timeout_sec`: Timeout for full research

## Legacy Skill Isolation

### How It Works

Legacy skills are now wrapped in `LegacySkillRegistry`:

```python
from app.skills.legacy_isolation import get_legacy_registry, set_legacy_enabled

# Disable by default
registry = get_legacy_registry()
assert registry.enabled is False

# Only load if explicitly enabled
set_legacy_enabled(True)
skills = registry.load_skills()
```

### Benefits

1. **No Auto-Registration**: Legacy skills don't pollute the namespace
2. **Feature Flag Control**: Enable/disable via configuration
3. **Clean Separation**: New and old systems don't interfere
4. **Graceful Degradation**: Works even if legacy imports fail

## Realtime Lookup Bundle

### Supported Intents

| Intent | Example Query | Tools |
|--------|---------------|-------|
| weather_lookup | "Weather in Melbourne?" | search_web, get_weather |
| exchange_rate_lookup | "USD to CNY rate" | search_web, get_exchange_rate |
| stock_quote_lookup | "AAPL price" | search_web, get_stock_quote |
| crypto_quote_lookup | "Bitcoin price" | search_web, get_crypto_quote |
| sports_score_lookup | "Lakers score" | search_web, get_sports_score |
| fuel_price_lookup | "Petrol price Sydney" | search_web, get_fuel_price |
| time_lookup | "Time in London" | search_web, get_time |

### Design Principles

1. **Speed**: Minimal lookup over full research
2. **Reliability**: Prefer search snippets from trusted sources
3. **Clarity**: Ask for missing key entities
4. **Safety**: Avoid hallucination with fallback responses

## Smart Router

### Intent Detection

The `RealtimeQueryDetector` uses regex patterns to identify:

- **Weather**: "天气", "weather", "forecast", "temperature"
- **Exchange**: "汇率", "USD to", "EUR", currency pairs
- **Stock**: "stock", "AAPL", "股票", ticker symbols
- **Crypto**: "bitcoin", "BTC", "以太坊", crypto symbols
- **Sports**: "score", "比分", "NBA", "英超"
- **Fuel**: "petrol", "diesel", "油价", "汽油"

### Routing Logic

```python
router = LazySkillRouter(config)
decision = router.route(query)

# decision.skill_bundle: "realtime_lookup" | "web_research" | "news_intelligence" | ...
# decision.confidence: 0.0 - 1.0
# decision.intent_type: specific intent classification
# decision.allow_fallback: whether fallback is permitted
```

## Testing

### Run Tests

```bash
# Test router
pytest tests/test_lazy_router.py -v

# Test legacy isolation
pytest tests/test_legacy_isolation.py -v

# All tests
pytest tests/ -v
```

### Test Coverage

- ✅ Realtime query detection (weather, rates, quotes, etc.)
- ✅ Routing decisions for different intents
- ✅ Legacy skill isolation and feature flags
- ✅ Fallback behavior
- ✅ Configuration handling

## Migration Checklist

### For Developers

- [ ] Review `lazy_router.py` for routing logic
- [ ] Review `legacy_isolation.py` for isolation pattern
- [ ] Review `realtime_lookup/` bundle structure
- [ ] Run tests: `pytest tests/test_lazy_router.py tests/test_legacy_isolation.py`
- [ ] Update any hardcoded skill references to use router

### For Operations

- [ ] Update `lazy_skills_config.json` in production
- [ ] Set `use_legacy_fallback: false` (legacy is now isolated)
- [ ] Set `prefer_realtime: true` (enable fast lookups)
- [ ] Monitor routing decisions in logs
- [ ] Track fallback rates

### For QA

- [ ] Test weather queries route to realtime_lookup
- [ ] Test exchange rate queries route to realtime_lookup
- [ ] Test stock quote queries route to realtime_lookup
- [ ] Test general research queries route to web_research
- [ ] Test news queries route to news_intelligence
- [ ] Verify fallback behavior when realtime fails
- [ ] Verify legacy skills don't load unless enabled

## Troubleshooting

### Realtime Queries Going to Web Research

**Problem**: Weather/rate queries not routing to realtime_lookup

**Solution**:
1. Check `prefer_realtime: true` in config
2. Check query matches realtime patterns
3. Enable debug logging: `enable_debug_logging: true`
4. Check logs for routing decision

### Legacy Skills Still Loading

**Problem**: Legacy skills loading despite isolation

**Solution**:
1. Check `use_legacy_fallback: false` in config
2. Verify `LegacySkillRegistry.enabled` is False
3. Check for explicit `set_legacy_enabled(True)` calls
4. Review imports in main entry point

### Timeouts on Realtime Queries

**Problem**: Realtime queries timing out

**Solution**:
1. Increase `realtime_timeout_sec` (default: 8)
2. Check external API availability (weather, rates, etc.)
3. Check network connectivity
4. Enable fallback to web_research if needed

## Next Steps

### Stage 3: Realtime Execution Engine

1. Implement `RealtimeSkillExecutor` class
2. Add tool implementations:
   - `get_weather()` - Open-Meteo API
   - `get_exchange_rate()` - Exchange rate API
   - `get_stock_quote()` - Stock quote API
   - `get_crypto_quote()` - Crypto quote API
   - `get_sports_score()` - Sports API
   - `get_fuel_price()` - Fuel price API
3. Add error handling and fallback logic
4. Integrate with tool exposure broker

### Stage 4: Testing & Monitoring

1. Add integration tests with real APIs
2. Add performance benchmarks
3. Add routing decision logging
4. Add fallback rate monitoring
5. Create dashboards for metrics

### Stage 5: Legacy Deprecation

1. Set deprecation timeline
2. Add warnings to legacy skill usage
3. Migrate remaining users
4. Remove legacy code

## References

- `app/skills/lazy_router.py` - Smart routing logic
- `app/skills/legacy_isolation.py` - Legacy skill isolation
- `app/skills/bundles/realtime_lookup/` - Realtime bundle
- `config/lazy_skills_config.json` - Configuration
- `tests/test_lazy_router.py` - Router tests
- `tests/test_legacy_isolation.py` - Isolation tests
