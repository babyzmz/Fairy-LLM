"""Execution Intelligence Layer — Stage 3 Core.

5-stage autonomous lookup:
  stage1 → structured provider (Open-Meteo/CoinGecko/Yahoo/exchangerate)
  stage2 → search snippet extraction
  stage3 → webpage open + text parse (critical for fuel prices)
  stage4 → multi-source cross-validation
  stage5 → web_research fallback

Supports parallel execution via asyncio for multi-intent queries.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable

from app.agents.realtime_lookup.query_analyzer import QueryAnalyzer, RealtimeIntent
from app.agents.realtime_lookup.result_builder import RealtimeResult, ResultBuilder
from app.agents.realtime_lookup.providers.weather_provider import WeatherProvider
from app.agents.realtime_lookup.providers.time_provider import TimeProvider
from app.agents.realtime_lookup.providers.crypto_provider import CryptoProvider
from app.agents.realtime_lookup.providers.stock_provider import StockProvider
from app.agents.realtime_lookup.providers.fx_provider import FxProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------

def _price(text: str) -> float | None:
    for pat in [
        r'\$([\d,]+\.?\d*)',
        r'([\d]+\.[\d]{1,3})\s*(?:AUD|USD|GBP|EUR)',
        r'\b([1-9]\d{0,4}\.\d{1,4})\b',
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                v = float(m.group(1).replace(',', ''))
                if 0 < v < 1_000_000:
                    return v
            except ValueError:
                pass
    return None


def _price_range(text: str) -> tuple[float, float] | None:
    m = re.search(r'([\d]+\.?[\d]*)\s*[\u2013\-]\s*([\d]+\.?[\d]*)', text)
    if m:
        try:
            a, b = float(m.group(1)), float(m.group(2))
            if 0 < a < 1_000_000 and 0 < b < 1_000_000:
                return (min(a, b), max(a, b))
        except ValueError:
            pass
    return None


def _rate(text: str) -> float | None:
    m = re.search(r'\b(\d+\.\d{2,6})\b', text)
    if m:
        try:
            v = float(m.group(1))
            if 0.00001 < v < 100_000:
                return v
        except ValueError:
            pass
    return None


def _temperature(text: str) -> float | None:
    m = re.search(r'(\d+(?:\.\d+)?)\s*\xb0[CF]', text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _condition(text: str) -> str:
    for w in ['sunny','clear','cloudy','overcast','rain','drizzle',
               'storm','snow','fog','windy',
               '\u6674','\u591a\u4e91','\u9634','\u96e8','\u96ea','\u96fe']:
        if w in text.lower():
            return w
    return text[:60].strip()


def _change_pct(text: str) -> float | None:
    m = re.search(r'([+\-]?\d+\.?\d*)%', text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Query reformulation banks
# ---------------------------------------------------------------------------

def _fuel_queries(location: str, fuel_type: str) -> list[str]:
    ft = fuel_type or 'petrol'
    nm = {'98': '98 RON premium', '95': '95 RON regular',
          '91': '91 RON', 'diesel': 'diesel', 'petrol': 'petrol'}
    fn = nm.get(ft, ft)
    return [
        f"{ft} petrol price {location} today",
        f"{fn} fuel price {location}",
        f"cheapest {fn} {location}",
        f"{location} fuel price today",
        f"petrol price near {location}",
    ]


def _weather_queries(location: str) -> list[str]:
    return [
        f"{location} weather today",
        f"current weather {location}",
        f"{location} temperature now",
    ]


def _crypto_queries(symbol: str) -> list[str]:
    return [
        f"{symbol} price USD today",
        f"{symbol} live price",
        f"{symbol} current price",
    ]


def _stock_queries(symbol: str) -> list[str]:
    return [
        f"{symbol} stock price today",
        f"{symbol} live stock price",
        f"{symbol} current price",
    ]


def _fx_queries(frm: str, to: str) -> list[str]:
    return [
        f"{frm} to {to} exchange rate today",
        f"{frm}/{to} rate",
        f"convert {frm} to {to}",
    ]


# ---------------------------------------------------------------------------
# Main engine — Stage 1 & 2
# ---------------------------------------------------------------------------

class LookupEngine:
    """5-stage autonomous realtime lookup engine."""

    def __init__(
        self,
        search_fn: Callable[[str], list[dict]] | None = None,
        fetch_page_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.search_fn = search_fn
        self.fetch_page_fn = fetch_page_fn
        self._analyzer = QueryAnalyzer()

    # --- public API ---

    def run(self, query: str) -> RealtimeResult:
        """Execute full 5-stage lookup for a single query string."""
        logger.info("lookup_engine_start query=%s", query[:60])
        intent = self._analyzer.analyze(query)
        if not intent.is_realtime:
            return ResultBuilder.failure(subtype="unknown", error_message="非实时查询")
        return self._dispatch(intent)

    def run_parallel(self, queries: list[str]) -> list[RealtimeResult]:
        """Run multiple lookups concurrently."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return list(pool.map(self.run, queries))
            return loop.run_until_complete(self._run_parallel_async(queries))
        except RuntimeError:
            return [self.run(q) for q in queries]

    async def _run_parallel_async(self, queries: list[str]) -> list[RealtimeResult]:
        loop = asyncio.get_event_loop()
        tasks = [loop.run_in_executor(None, self.run, q) for q in queries]
        return list(await asyncio.gather(*tasks))

    # --- dispatch ---

    def _dispatch(self, intent: RealtimeIntent) -> RealtimeResult:
        handlers = {
            "weather":  self._handle_weather,
            "time":     self._handle_time,
            "crypto":   self._handle_crypto,
            "stock":    self._handle_stock,
            "exchange": self._handle_exchange,
            "fuel":     self._handle_fuel,
            "sports":   self._handle_sports,
        }
        handler = handlers.get(intent.subtype)
        if not handler:
            return ResultBuilder.failure(subtype=intent.subtype)
        try:
            result = handler(intent)
            if result and result.success:
                logger.info("card_built subtype=%s stage=%s", intent.subtype, result.stage_used)
                return result
        except Exception as exc:
            logger.exception("dispatch_error subtype=%s error=%s", intent.subtype, exc)
        return self._stage5_fallback(intent)

    # --- Stage 1: structured providers ---

    def _s1_weather(self, location: str) -> RealtimeResult | None:
        logger.info("realtime_stage_entered stage=1 subtype=weather")
        data = WeatherProvider.fetch(location)
        if data and data.get("temperature_c") is not None:
            logger.info("provider_success subtype=weather location=%s", location)
            return ResultBuilder.weather(location, data, confidence=0.95, stage="stage1")
        return None

    def _s1_time(self, location: str) -> RealtimeResult | None:
        logger.info("realtime_stage_entered stage=1 subtype=time")
        data = TimeProvider.fetch(location)
        if data:
            logger.info("provider_success subtype=time location=%s", location)
            return ResultBuilder.time(location, data, confidence=0.99, stage="stage1")
        return None

    def _s1_crypto(self, symbol: str) -> RealtimeResult | None:
        logger.info("realtime_stage_entered stage=1 subtype=crypto")
        data = CryptoProvider.fetch(symbol)
        if data and data.get("price_usd") is not None:
            logger.info("provider_success subtype=crypto symbol=%s", symbol)
            return ResultBuilder.crypto(symbol, data, confidence=0.95, stage="stage1")
        return None

    def _s1_stock(self, symbol: str) -> RealtimeResult | None:
        logger.info("realtime_stage_entered stage=1 subtype=stock")
        data = StockProvider.fetch(symbol)
        if data and data.get("price_usd") is not None:
            logger.info("provider_success subtype=stock symbol=%s", symbol)
            return ResultBuilder.stock(symbol, data, confidence=0.92, stage="stage1")
        return None

    def _s1_fx(self, frm: str, to: str) -> RealtimeResult | None:
        logger.info("realtime_stage_entered stage=1 subtype=exchange")
        data = FxProvider.fetch(frm, to)
        if data and data.get("rate") is not None:
            logger.info("provider_success subtype=exchange from=%s to=%s", frm, to)
            return ResultBuilder.fx(data, confidence=0.95, stage="stage1")
        return None

    # --- Stage 2: snippet extraction ---

    def _s2_search(self, queries: list[str], subtype: str, **kw: Any) -> RealtimeResult | None:
        if not self.search_fn:
            return None
        logger.info("realtime_stage_entered stage=2 subtype=%s", subtype)
        for query in queries:
            try:
                results = self.search_fn(query, max_results=5)
            except Exception as exc:
                logger.debug("s2_search_error q=%s err=%s", query, exc)
                continue
            if not results:
                continue
            combined = " ".join(
                r.get("snippet", "") + " " + r.get("title", "")
                for r in results[:3]
            )
            urls = [r.get("url", "") for r in results if r.get("url")]
            result = self._parse_snippet(combined, subtype, urls=urls, **kw)
            if result:
                logger.info("snippet_extracted subtype=%s", subtype)
                return result
        return None

    def _parse_snippet(self, text: str, subtype: str, urls: list[str] | None = None, **kw: Any) -> RealtimeResult | None:
        if subtype == "weather":
            t = _temperature(text)
            if t is not None:
                return ResultBuilder.weather(
                    kw.get("location", ""),
                    {"temperature_c": t, "condition": _condition(text)},
                    confidence=0.75, stage="stage2", source_urls=urls or [],
                )
        elif subtype == "crypto":
            p = _price(text)
            if p:
                return ResultBuilder.crypto(
                    kw.get("symbol", ""),
                    {"price_usd": p, "change_24h_pct": _change_pct(text)},
                    confidence=0.78, stage="stage2", source_urls=urls or [],
                )
        elif subtype == "stock":
            p = _price(text)
            if p:
                return ResultBuilder.stock(
                    kw.get("symbol", ""),
                    {"price_usd": p, "change_pct": _change_pct(text), "company": kw.get("symbol", "")},
                    confidence=0.75, stage="stage2", source_urls=urls or [],
                )
        elif subtype == "exchange":
            r = _rate(text)
            if r:
                return ResultBuilder.fx(
                    {"from_currency": kw.get("from_currency", ""),
                     "to_currency": kw.get("to_currency", ""), "rate": r},
                    confidence=0.75, stage="stage2", source_urls=urls or [],
                )
        elif subtype == "fuel":
            pr = _price_range(text)
            if pr:
                return ResultBuilder.fuel(
                    kw.get("location", ""), kw.get("fuel_type", "petrol"),
                    pr[0], pr[1], confidence=0.70, stage="stage2", source_urls=urls or [],
                )
            p = _price(text)
            if p:
                return ResultBuilder.fuel(
                    kw.get("location", ""), kw.get("fuel_type", "petrol"),
                    p, p, confidence=0.65, stage="stage2", source_urls=urls or [],
                )
        return None

    # --- Stage 3: webpage open + DOM parse ---

    def _s3_webpage(self, queries: list[str], subtype: str, max_pages: int = 3, **kw: Any) -> RealtimeResult | None:
        if not self.search_fn or not self.fetch_page_fn:
            return None
        logger.info("realtime_stage_entered stage=3 subtype=%s", subtype)
        for query in queries:
            try:
                results = self.search_fn(query, max_results=5)
            except Exception:
                continue
            urls = [r.get("url", "") for r in results if r.get("url")]
            for url in urls[:max_pages]:
                try:
                    logger.info("webpage_opened url=%s", url)
                    page_text = self.fetch_page_fn(url)
                    if not page_text:
                        continue
                    result = self._parse_page(page_text, subtype, url=url, **kw)
                    if result:
                        logger.info("structured_value_found stage=3 subtype=%s url=%s", subtype, url)
                        return result
                except Exception as exc:
                    logger.debug("s3_page_error url=%s err=%s", url, exc)
        return None

    def _parse_page(self, text: str, subtype: str, url: str = "", **kw: Any) -> RealtimeResult | None:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        hits = [l for l in lines if re.search(r'\d+\.\d', l)]
        combined = " ".join(hits[:20]) if hits else text[:2000]
        return self._parse_snippet(combined, subtype, urls=[url] if url else [], **kw)

    # --- Stage 4: multi-source validation ---

    def _s4_validate(self, primary: RealtimeResult, queries: list[str], subtype: str, **kw: Any) -> RealtimeResult:
        if not self.search_fn or primary.numeric_value is None:
            return primary
        logger.info("realtime_stage_entered stage=4 subtype=%s", subtype)
        try:
            results = self.search_fn(queries[-1] if queries else "", max_results=3)
            if not results:
                return primary
            combined = " ".join(r.get("snippet", "") for r in results[:2])
            second = self._parse_snippet(combined, subtype, **kw)
            if second and second.numeric_value is not None:
                dev = abs(primary.numeric_value - second.numeric_value) / max(abs(primary.numeric_value), 0.001)
                if dev < 0.15:
                    logger.info("multi_source_validated subtype=%s deviation=%.3f", subtype, dev)
                    import dataclasses
                    return dataclasses.replace(
                        primary,
                        confidence=min(primary.confidence + 0.05, 1.0),
                        source_urls=primary.source_urls + second.source_urls,
                        stage_used="stage4",
                    )
        except Exception as exc:
            logger.debug("s4_error err=%s", exc)
        return primary

    # --- Stage 5: fallback ---

    def _stage5_fallback(self, intent: RealtimeIntent) -> RealtimeResult:
        logger.info("realtime_stage_entered stage=5 subtype=%s", intent.subtype)
        if self.search_fn:
            try:
                results = self.search_fn(intent.raw_query, max_results=3)
                if results:
                    snippet = results[0].get("snippet", "")
                    url = results[0].get("url", "")
                    if snippet:
                        from datetime import datetime as _dt
                        card = {
                            "type": "generic_card", "card_type": "numeric",
                            "title": intent.raw_query[:60], "primary": snippet[:120],
                            "timestamp": _dt.utcnow().isoformat() + "Z", "confidence": 0.45,
                        }
                        return RealtimeResult(
                            success=True, speech_text=snippet[:200], card_payload=card,
                            numeric_value=None, confidence=0.45,
                            source_urls=[url] if url else [],
                            stage_used="stage5", subtype=intent.subtype,
                        )
            except Exception as exc:
                logger.debug("s5_error err=%s", exc)
        return ResultBuilder.failure(subtype=intent.subtype, error_message="实时数据暂不可用", stage="stage5")

    # --- Subtype handlers ---

    def _handle_weather(self, intent: RealtimeIntent) -> RealtimeResult | None:
        loc = intent.entities.get("location", "")
        if not loc:
            return None
        r = self._s1_weather(loc)
        if r:
            return self._s4_validate(r, _weather_queries(loc), "weather", location=loc)
        r = self._s2_search(_weather_queries(loc), "weather", location=loc)
        if r:
            return self._s4_validate(r, _weather_queries(loc), "weather", location=loc)
        return self._s3_webpage(_weather_queries(loc), "weather", location=loc)

    def _handle_time(self, intent: RealtimeIntent) -> RealtimeResult | None:
        loc = intent.entities.get("location", "")
        if not loc:
            return None
        return self._s1_time(loc)  # time always uses Stage 1 (ZoneInfo)

    def _handle_crypto(self, intent: RealtimeIntent) -> RealtimeResult | None:
        sym = intent.entities.get("symbol", "")
        if not sym:
            return None
        r = self._s1_crypto(sym)
        if r:
            return self._s4_validate(r, _crypto_queries(sym), "crypto", symbol=sym)
        r = self._s2_search(_crypto_queries(sym), "crypto", symbol=sym)
        if r:
            return self._s4_validate(r, _crypto_queries(sym), "crypto", symbol=sym)
        return self._s3_webpage(_crypto_queries(sym), "crypto", symbol=sym)

    def _handle_stock(self, intent: RealtimeIntent) -> RealtimeResult | None:
        sym = intent.entities.get("symbol", "")
        if not sym:
            return None
        r = self._s1_stock(sym)
        if r:
            return self._s4_validate(r, _stock_queries(sym), "stock", symbol=sym)
        r = self._s2_search(_stock_queries(sym), "stock", symbol=sym)
        if r:
            return self._s4_validate(r, _stock_queries(sym), "stock", symbol=sym)
        return self._s3_webpage(_stock_queries(sym), "stock", symbol=sym)

    def _handle_exchange(self, intent: RealtimeIntent) -> RealtimeResult | None:
        frm = intent.entities.get("from_currency", "")
        to  = intent.entities.get("to_currency", "")
        if not frm or not to:
            return None
        r = self._s1_fx(frm, to)
        if r:
            return self._s4_validate(r, _fx_queries(frm, to), "exchange", from_currency=frm, to_currency=to)
        r = self._s2_search(_fx_queries(frm, to), "exchange", from_currency=frm, to_currency=to)
        if r:
            return self._s4_validate(r, _fx_queries(frm, to), "exchange", from_currency=frm, to_currency=to)
        return self._s3_webpage(_fx_queries(frm, to), "exchange", from_currency=frm, to_currency=to)

    def _handle_fuel(self, intent: RealtimeIntent) -> RealtimeResult | None:
        loc = intent.entities.get("location", "")
        ft  = intent.entities.get("fuel_type", "petrol")
        if not loc:
            return None
        qs = _fuel_queries(loc, ft)
        # Fuel: no stage-1 provider → stage2 → stage3 (CRITICAL)
        r = self._s2_search(qs, "fuel", location=loc, fuel_type=ft)
        if r:
            return self._s4_validate(r, qs, "fuel", location=loc, fuel_type=ft)
        return self._s3_webpage(qs, "fuel", max_pages=4, location=loc, fuel_type=ft)

    def _handle_sports(self, intent: RealtimeIntent) -> RealtimeResult | None:
        event = intent.entities.get("event", "")
        if not event:
            return None
        qs = [f"{event} score today", f"{event} latest result", f"{event} game score"]
        r = self._s2_search(qs, "sports", event=event)
        if not r:
            r = self._s3_webpage(qs, "sports", event=event)
        return r
