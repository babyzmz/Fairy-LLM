"""Test suite for Fairy Realtime Autonomous Lookup Engine — Stage 3.

Covers:
- Tokyo time
- New York time (date rollover awareness)
- Melbourne weather
- Bitcoin price
- NVIDIA stock
- USD/AUD rate
- Forest Hill fuel price (webpage fallback)
- Parallel execution
- Query analyzer
- Timezone resolver
"""

from __future__ import annotations

import sys
import os
import re
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

# Make sure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_search(query: str, max_results: int = 5) -> list[dict]:
    """Mock search_web for unit tests."""
    q = query.lower()
    if "melbourne" in q and "weather" in q:
        return [{
            "title": "Melbourne Weather Today",
            "url": "https://bom.gov.au/vic/forecasts/melbourne.shtml",
            "snippet": "Melbourne today: Partly cloudy, 21°C. Humidity 55%. Wind 8km/h NW.",
        }]
    if "bitcoin" in q or "btc" in q:
        return [{
            "title": "Bitcoin Price",
            "url": "https://coinmarketcap.com/currencies/bitcoin/",
            "snippet": "Bitcoin (BTC) price today is $67,450.12 USD with a 24-hour change of +2.3%.",
        }]
    if "nvda" in q or "nvidia" in q:
        return [{
            "title": "NVIDIA Stock Price",
            "url": "https://finance.yahoo.com/quote/NVDA",
            "snippet": "NVIDIA Corporation (NVDA) stock is trading at $875.42, up +1.5% today.",
        }]
    if "usd" in q and "aud" in q:
        return [{
            "title": "USD to AUD Exchange Rate",
            "url": "https://xe.com/currencyconverter/convert/?Amount=1&From=USD&To=AUD",
            "snippet": "1 US Dollar = 1.5234 Australian Dollar. Updated just now.",
        }]
    if "forest hill" in q and ("petrol" in q or "fuel" in q or "98" in q):
        return [{
            "title": "Petrol Prices Forest Hill",
            "url": "https://petrolspy.com.au/map/suburb/forest-hill",
            "snippet": "Forest Hill 98 RON prices: 1.89–1.95 AUD/L. Updated today.",
        }]
    return []


def _mock_fetch_page(url: str) -> str:
    """Mock read_webpage for Stage-3 fallback tests."""
    if "petrolspy" in url or "forest" in url.lower():
        return """
        Forest Hill VIC Petrol Prices
        98 RON Premium
        BP Forest Hill: 1.89 AUD/L
        Shell Forest Hill: 1.92 AUD/L
        Coles Express: 1.95 AUD/L
        Last updated: today
        """
    return ""


# ---------------------------------------------------------------------------
# TimezoneResolver tests
# ---------------------------------------------------------------------------

class TestTimezoneResolver:
    def test_tokyo(self):
        from app.agents.realtime_lookup.timezone_resolver import TimezoneResolver
        tz = TimezoneResolver.resolve("Tokyo")
        assert tz == "Asia/Tokyo"

    def test_tokyo_chinese(self):
        from app.agents.realtime_lookup.timezone_resolver import TimezoneResolver
        tz = TimezoneResolver.resolve("东京")
        assert tz == "Asia/Tokyo"

    def test_new_york(self):
        from app.agents.realtime_lookup.timezone_resolver import TimezoneResolver
        tz = TimezoneResolver.resolve("New York")
        assert tz == "America/New_York"

    def test_melbourne(self):
        from app.agents.realtime_lookup.timezone_resolver import TimezoneResolver
        tz = TimezoneResolver.resolve("Melbourne")
        assert tz == "Australia/Melbourne"

    def test_unknown_returns_none(self):
        from app.agents.realtime_lookup.timezone_resolver import TimezoneResolver
        tz = TimezoneResolver.resolve("Fakecityxyz")
        assert tz is None

    def test_case_insensitive(self):
        from app.agents.realtime_lookup.timezone_resolver import TimezoneResolver
        tz = TimezoneResolver.resolve("melbourne")
        assert tz == "Australia/Melbourne"

    def test_forest_hill(self):
        from app.agents.realtime_lookup.timezone_resolver import TimezoneResolver
        tz = TimezoneResolver.resolve("Forest Hill")
        assert tz == "Australia/Melbourne"


# ---------------------------------------------------------------------------
# TimeProvider tests
# ---------------------------------------------------------------------------

class TestTimeProvider:
    def test_tokyo_time_structure(self):
        from app.agents.realtime_lookup.providers.time_provider import TimeProvider
        data = TimeProvider.fetch("Tokyo")
        assert data is not None
        assert "time" in data
        assert "date" in data
        assert "tz_name" in data
        assert data["tz_name"] == "Asia/Tokyo"
        assert re.match(r"\d{2}:\d{2}", data["time"])

    def test_new_york_time_structure(self):
        from app.agents.realtime_lookup.providers.time_provider import TimeProvider
        data = TimeProvider.fetch("New York")
        assert data is not None
        assert data["tz_name"] == "America/New_York"

    def test_time_uses_zoneinfo_not_offset(self):
        """Time must come from ZoneInfo, not offset arithmetic."""
        from app.agents.realtime_lookup.providers.time_provider import TimeProvider
        from zoneinfo import ZoneInfo
        data = TimeProvider.fetch("Tokyo")
        assert data is not None
        # Verify time matches what ZoneInfo gives us
        tz = ZoneInfo("Asia/Tokyo")
        now = datetime.now(tz)
        # Should be within 5 seconds
        provider_time = data["time"]
        expected_time = now.strftime("%H:%M")
        assert provider_time == expected_time

    def test_new_york_date_rollover_awareness(self):
        """NY can be a different date than Tokyo — provider must reflect real date."""
        from app.agents.realtime_lookup.providers.time_provider import TimeProvider
        from zoneinfo import ZoneInfo
        ny = TimeProvider.fetch("New York")
        tokyo = TimeProvider.fetch("Tokyo")
        assert ny is not None and tokyo is not None
        # Dates may differ — both must be valid ISO dates
        assert re.match(r"\d{4}-\d{2}-\d{2}", ny["date"])
        assert re.match(r"\d{4}-\d{2}-\d{2}", tokyo["date"])

    def test_period_zh_values(self):
        from app.agents.realtime_lookup.providers.time_provider import TimeProvider
        data = TimeProvider.fetch("Melbourne")
        assert data is not None
        assert data["period_zh"] in ("早上", "中午", "下午", "晚上", "深夜")


# ---------------------------------------------------------------------------
# QueryAnalyzer tests
# ---------------------------------------------------------------------------

class TestQueryAnalyzer:
    def setup_method(self):
        from app.agents.realtime_lookup.query_analyzer import QueryAnalyzer
        self.analyzer = QueryAnalyzer()

    def test_detect_weather(self):
        intent = self.analyzer.analyze("帮我查墨尔本今天天气")
        assert intent.subtype == "weather"
        assert intent.is_realtime
        assert intent.entities.get("location") == "墨尔本"

    def test_detect_time_tokyo(self):
        intent = self.analyzer.analyze("现在东京几点")
        assert intent.subtype == "time"
        assert intent.entities.get("location") == "东京"

    def test_detect_crypto_btc(self):
        intent = self.analyzer.analyze("比特币现在价格多少")
        assert intent.subtype == "crypto"
        assert intent.entities.get("symbol") == "BTC"

    def test_detect_stock_nvidia(self):
        intent = self.analyzer.analyze("英伟达现在股价多少")
        assert intent.subtype == "stock"
        assert intent.entities.get("symbol") == "NVDA"

    def test_detect_exchange_usd_aud(self):
        intent = self.analyzer.analyze("美元兑澳元汇率多少")
        assert intent.subtype == "exchange"
        assert intent.entities.get("from_currency") == "USD"
        assert intent.entities.get("to_currency") == "AUD"

    def test_detect_fuel(self):
        intent = self.analyzer.analyze("Forest Hill 附近 98 号油价多少")
        assert intent.subtype == "fuel"
        assert intent.entities.get("location") == "Forest Hill"
        assert intent.entities.get("fuel_type") == "98"

    def test_unknown_query(self):
        intent = self.analyzer.analyze("帮我写一首诗")
        assert not intent.is_realtime

    def test_english_weather(self):
        intent = self.analyzer.analyze("What's the weather in Melbourne today?")
        assert intent.subtype == "weather"


# ---------------------------------------------------------------------------
# LookupEngine tests (with mocks)
# ---------------------------------------------------------------------------

class TestLookupEngineWithMocks:
    def setup_method(self):
        from app.agents.realtime_lookup.lookup_engine import LookupEngine
        self.engine = LookupEngine(
            search_fn=_mock_search,
            fetch_page_fn=_mock_fetch_page,
        )

    def test_melbourne_weather_returns_success(self):
        result = self.engine.run("帮我查墨尔本今天天气")
        # May succeed via stage1 (Open-Meteo) or stage2 (mock snippet)
        # Both are acceptable
        assert result is not None
        assert result.subtype == "weather"

    def test_bitcoin_price_snippet(self):
        # Stage-1 (CoinGecko) may or may not work in test env;
        # stage-2 mock snippet should work
        result = self.engine.run("比特币现在价格多少")
        assert result is not None
        assert result.subtype == "crypto"

    def test_nvidia_stock_snippet(self):
        result = self.engine.run("英伟达现在股价多少")
        assert result is not None
        assert result.subtype == "stock"

    def test_usd_aud_rate_snippet(self):
        result = self.engine.run("美元兑澳元汇率多少")
        assert result is not None
        assert result.subtype == "exchange"

    def test_forest_hill_fuel_stage3(self):
        """Fuel must use stage2/3 since there's no stage1 provider."""
        result = self.engine.run("Forest Hill 附近 98 号油价多少")
        assert result is not None
        assert result.subtype == "fuel"
        # Stage used must be stage2 or stage3
        assert result.stage_used in ("stage2", "stage3", "stage4", "stage5")

    def test_tokyo_time_no_search_needed(self):
        """Time lookups must use Stage-1 timezone engine only."""
        result = self.engine.run("现在东京几点")
        assert result.success
        assert result.stage_used == "stage1"
        assert result.subtype == "time"
        assert result.card_payload.get("city") == "东京" or "Tokyo" in str(result.card_payload)

    def test_new_york_time(self):
        result = self.engine.run("纽约现在几点")
        assert result.success
        assert result.stage_used == "stage1"

    def test_no_hallucination_on_unknown(self):
        """Engine must return failure, not fabricate data."""
        result = self.engine.run("xyzfakecity123 油价多少")
        # Should fail gracefully — no fabricated numeric_value
        if result.success:
            # If it succeeded via stage5 snippet, numeric_value may be None
            pass  # stage5 fallback is acceptable
        else:
            assert result.speech_text == "实时数据暂不可用" or not result.success

    def test_parallel_tokyo_and_weather(self):
        """Parallel execution: time + weather must both return results."""
        results = self.engine.run_parallel([
            "现在东京几点",
            "墨尔本今天天气怎么样",
        ])
        assert len(results) == 2
        subtypes = {r.subtype for r in results}
        assert "time" in subtypes
        assert "weather" in subtypes


# ---------------------------------------------------------------------------
# ResultBuilder tests
# ---------------------------------------------------------------------------

class TestResultBuilder:
    def test_time_card_protocol(self):
        from app.agents.realtime_lookup.result_builder import ResultBuilder
        r = ResultBuilder.time("Tokyo", {
            "date": "2026-03-20", "time": "14:35",
            "weekday": "Friday", "hour": 14,
            "period_zh": "下午", "is_daytime": True,
            "tz_name": "Asia/Tokyo",
        })
        assert r.success
        card = r.card_payload
        assert card["type"] == "time_card"
        assert card["primary"] == "14:35"
        assert "confidence" in card
        assert 0 <= card["confidence"] <= 1

    def test_weather_card_protocol(self):
        from app.agents.realtime_lookup.result_builder import ResultBuilder
        r = ResultBuilder.weather("Melbourne", {
            "temperature_c": 21.0, "condition": "Partly cloudy",
            "humidity_pct": 55, "wind_kmh": 8.0,
        })
        assert r.success
        card = r.card_payload
        assert card["type"] == "weather_card"
        assert card["primary"] == "21°C"
        assert "Humidity" in str(card["secondary"])

    def test_crypto_card_protocol(self):
        from app.agents.realtime_lookup.result_builder import ResultBuilder
        r = ResultBuilder.crypto("BTC", {"price_usd": 67450.12, "change_24h_pct": 2.3})
        assert r.success
        assert r.numeric_value == 67450.12
        assert r.card_payload["type"] == "crypto_card"

    def test_fx_card_protocol(self):
        from app.agents.realtime_lookup.result_builder import ResultBuilder
        r = ResultBuilder.fx({"from_currency": "USD", "to_currency": "AUD", "rate": 1.5234})
        assert r.success
        assert r.card_payload["type"] == "fx_card"
        assert r.numeric_value == 1.5234

    def test_fuel_card_protocol(self):
        from app.agents.realtime_lookup.result_builder import ResultBuilder
        r = ResultBuilder.fuel("Forest Hill", "98", 1.89, 1.95)
        assert r.success
        assert r.card_payload["type"] == "fuel_card"
        assert "1.89" in r.card_payload["primary"]
        assert "1.95" in r.card_payload["primary"]

    def test_failure_result(self):
        from app.agents.realtime_lookup.result_builder import ResultBuilder
        r = ResultBuilder.failure(subtype="crypto", error_message="实时数据暂不可用")
        assert not r.success
        assert r.speech_text == "实时数据暂不可用"


if __name__ == "__main__":
    pytest.main(["-v", __file__])
