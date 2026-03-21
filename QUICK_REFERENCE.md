# Quick Reference: Lazy Skill Router

## TL;DR

**Problem**: Legacy and lazy skills coexist, causing duplicated capabilities and inconsistent routing.

**Solution**:
1. Isolate legacy skills (feature-flagged)
2. Add realtime_lookup bundle for fast queries
3. Smart router with intent detection

**Result**: Clean separation, fast queries, smart routing.

## Quick Start

### 1. Load Configuration
```python
from app.skills.skill_dispatcher import SkillDispatcher

dispatcher = SkillDispatcher("config/lazy_skills_config.json")
```

### 2. Route Query
```python
decision = dispatcher.dispatch("What's the weather in Melbourne?")
print(decision.skill_bundle)  # "realtime_lookup"
print(decision.intent_type)   # "weather_lookup"
print(decision.confidence)    # 0.95
```

### 3. Execute Query
```python
result = dispatcher.execute("USD to CNY exchange rate")
# Returns execution result
```

## Configuration

```json
{
  "use_lazy_skills": true,
  "use_legacy_fallback": false,
  "prefer_realtime": true,
  "realtime_timeout_sec": 8,
  "web_research_timeout_sec": 15
}
```

## Routing Rules

| Query Type | Skill Bundle | Timeout |
|-----------|--------------|---------|
| Weather | realtime_lookup | 8s |
| Exchange rate | realtime_lookup | 8s |
| Stock quote | realtime_lookup | 8s |
| Crypto quote | realtime_lookup | 8s |
| Sports score | realtime_lookup | 8s |
| Fuel price | realtime_lookup | 8s |
| News | news_intelligence | 15s |
| Document edit | document_editing | 15s |
| Terminal task | terminal_agent | 15s |
| General query | web_research | 15s |

## Legacy Skills

### Disable (Default)
```python
from app.skills.legacy_isolation import set_legacy_enabled

set_legacy_enabled(False)  # Legacy skills won't load
```

### Enable (If Needed)
```python
set_legacy_enabled(True)  # Legacy skills will load
```

## Testing

```bash
# Run all tests
pytest tests/test_lazy_router.py tests/test_legacy_isolation.py -v

# Run specific test
pytest tests/test_lazy_router.py::TestRealtimeQueryDetector -v

# Run with coverage
pytest tests/ --cov=app/skills
```

## Common Queries

### Weather
```python
dispatcher.dispatch("What's the weather in Melbourne?")
# → realtime_lookup, weather_lookup, 0.95 confidence
```

### Exchange Rate
```python
dispatcher.dispatch("USD to CNY exchange rate")
# → realtime_lookup, exchange_rate_lookup, 0.90 confidence
```

### Stock Quote
```python
dispatcher.dispatch("AAPL stock price")
# → realtime_lookup, stock_quote_lookup, 0.85 confidence
```

### Crypto Quote
```python
dispatcher.dispatch("Bitcoin price")
# → realtime_lookup, crypto_quote_lookup, 0.90 confidence
```

### Sports Score
```python
dispatcher.dispatch("Lakers vs Celtics score")
# → realtime_lookup, sports_score_lookup, 0.80 confidence
```

### Fuel Price
```python
dispatcher.dispatch("Petrol price in Sydney")
# → realtime_lookup, fuel_price_lookup, 0.85 confidence
```

### News
```python
dispatcher.dispatch("Latest tech news")
# → news_intelligence, news_lookup, 0.85 confidence
```

### General Query
```python
dispatcher.dispatch("How to learn Python?")
# → web_research, general_research, 0.70 confidence
```

## Debugging

### Enable Debug Logging
```json
{
  "enable_debug_logging": true
}
```

### Check Routing Decision
```python
decision = dispatcher.dispatch(query)
print(f"Bundle: {decision.skill_bundle}")
print(f"Intent: {decision.intent_type}")
print(f"Confidence: {decision.confidence}")
print(f"Reason: {decision.reason}")
print(f"Fallback allowed: {decision.allow_fallback}")
```

### Check Legacy Status
```python
from app.skills.legacy_isolation import get_legacy_registry

registry = get_legacy_registry()
print(f"Enabled: {registry.enabled}")
print(f"Loaded: {registry._loaded}")
print(f"Skills: {list(registry._skills.keys())}")
```

## Files Reference

| File | Purpose |
|------|---------|
| `app/skills/lazy_router.py` | Smart routing logic |
| `app/skills/legacy_isolation.py` | Legacy skill isolation |
| `app/skills/skill_dispatcher.py` | Main dispatcher |
| `app/skills/bundles/realtime_lookup/` | Realtime bundle |
| `config/lazy_skills_config.json` | Configuration |
| `tests/test_lazy_router.py` | Router tests |
| `tests/test_legacy_isolation.py` | Isolation tests |
| `MIGRATION_GUIDE.md` | Full migration guide |
| `REALTIME_IMPLEMENTATION_ROADMAP.md` | Implementation plan |

## Troubleshooting

### Realtime queries going to web_research
- Check `prefer_realtime: true` in config
- Check query matches realtime patterns
- Enable debug logging

### Legacy skills still loading
- Check `use_legacy_fallback: false` in config
- Check `set_legacy_enabled(False)` is called
- Review imports in main entry point

### Timeouts on realtime queries
- Increase `realtime_timeout_sec` (default: 8)
- Check external API availability
- Check network connectivity

## Next Steps

1. **Integrate dispatcher** into main application
2. **Run tests** to verify routing
3. **Monitor logs** for routing decisions
4. **Implement Stage 3** - Realtime execution engine
5. **Add metrics** for monitoring

## Support

- **Router logic**: See `app/skills/lazy_router.py`
- **Legacy isolation**: See `app/skills/legacy_isolation.py`
- **Integration**: See `app/skills/skill_dispatcher.py`
- **Full guide**: See `MIGRATION_GUIDE.md`
- **Implementation**: See `REALTIME_IMPLEMENTATION_ROADMAP.md`
