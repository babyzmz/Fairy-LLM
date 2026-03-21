"""Query Understanding Layer — Stage 3.

Detects realtime intent subtype and extracts entities.
Supports multilingual queries (Chinese + English).
Never fabricates entity values; returns None when uncertain.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

Subtype = Literal[
    "weather", "time", "crypto", "stock", "exchange",
    "fuel", "sports", "countdown", "unknown",
]


@dataclass
class RealtimeIntent:
    """Result of query analysis."""

    subtype: Subtype
    confidence: float
    entities: dict[str, str] = field(default_factory=dict)
    raw_query: str = ""

    @property
    def is_realtime(self) -> bool:
        return self.subtype != "unknown" and self.confidence >= 0.65


# ---------------------------------------------------------------------------
# Entity extraction helpers
# ---------------------------------------------------------------------------

_LOCATIONS: list[str] = [
    # Australia
    "Melbourne", "墨尔本", "Sydney", "悉尼", "Brisbane", "布里斯班",
    "Perth", "珀斯", "Adelaide", "阿德莱德", "Canberra", "堪培拉",
    "Hobart", "Darwin", "Forest Hill",
    # Asia
    "Tokyo", "东京", "Osaka", "大阪", "Beijing", "北京",
    "Shanghai", "上海", "Shenzhen", "深圳", "Guangzhou", "广州",
    "Chengdu", "成都", "Hangzhou", "杭州", "Wuhan", "武汉",
    "Hong Kong", "香港", "Taipei", "台北", "Seoul", "首尔",
    "Singapore", "新加坡", "Bangkok", "曼谷", "Jakarta", "雅加达",
    "Kuala Lumpur", "吉隆坡", "Dubai", "迪拜",
    "Mumbai", "孟买", "New Delhi", "Delhi", "新德里",
    # Europe
    "London", "伦敦", "Paris", "巴黎", "Berlin", "柏林",
    "Moscow", "莫斯科", "Amsterdam", "Madrid", "马德里",
    "Rome", "罗马", "Vienna", "维也纳", "Istanbul", "伊斯坦布尔",
    # Americas
    "New York", "纽约", "Los Angeles", "洛杉矶", "Chicago", "芝加哥",
    "Toronto", "多伦多", "Vancouver", "温哥华",
    "Mexico City", "墨西哥城", "Sao Paulo", "圣保罗",
    "Buenos Aires", "布宜诺斯艾利斯", "Santiago", "圣地亚哥",
    # Africa / Pacific
    "Cairo", "开罗", "Lagos", "Nairobi", "Johannesburg",
    "Cape Town", "开普敦", "Auckland", "奥克兰", "Honolulu",
]

# Sort longest-first so multi-word names match before single words
_LOCATIONS.sort(key=len, reverse=True)

_CRYPTO_SYMBOLS: dict[str, str] = {
    "比特币": "BTC", "bitcoin": "BTC", "btc": "BTC",
    "以太坊": "ETH", "ethereum": "ETH", "eth": "ETH",
    "狗狗币": "DOGE", "dogecoin": "DOGE", "doge": "DOGE",
    "瑞波": "XRP", "ripple": "XRP", "xrp": "XRP",
    "sol": "SOL", "solana": "SOL",
    "ada": "ADA", "cardano": "ADA",
    "avax": "AVAX", "avalanche": "AVAX",
    "bnb": "BNB", "binance": "BNB",
}

_STOCK_SYMBOLS: dict[str, str] = {
    "英伟达": "NVDA", "nvidia": "NVDA", "nvda": "NVDA",
    "苹果": "AAPL", "apple": "AAPL", "aapl": "AAPL",
    "微软": "MSFT", "microsoft": "MSFT", "msft": "MSFT",
    "特斯拉": "TSLA", "tesla": "TSLA", "tsla": "TSLA",
    "谷歌": "GOOGL", "google": "GOOGL", "googl": "GOOGL",
    "亚马逊": "AMZN", "amazon": "AMZN", "amzn": "AMZN",
    "meta": "META", "facebook": "META",
    "netflix": "NFLX", "nflx": "NFLX",
}

_CURRENCY_CODES: dict[str, str] = {
    "美元": "USD", "dollar": "USD", "usd": "USD",
    "欧元": "EUR", "euro": "EUR",   "eur": "EUR",
    "英镑": "GBP", "pound": "GBP", "gbp": "GBP",
    "人民币": "CNY", "rmb": "CNY",  "cny": "CNY",
    "日元": "JPY", "yen": "JPY",   "jpy": "JPY",
    "澳元": "AUD", "australian dollar": "AUD", "aud": "AUD",
    "加元": "CAD", "canadian dollar": "CAD",  "cad": "CAD",
    "港元": "HKD", "hkd": "HKD",
    "新元": "SGD", "sgd": "SGD",
    "韩元": "KRW", "krw": "KRW",
    "瑞郎": "CHF", "chf": "CHF",
    "nzd": "NZD", "纽元": "NZD",
}

_FUEL_TYPES: dict[str, str] = {
    "98号": "98", "98 号": "98", "98ron": "98",
    "premium": "98",
    "95号": "95", "95 号": "95", "95ron": "95",
    "regular": "95",
    "91号": "91", "91 号": "91",
    "e10": "E10",
    "柴油": "diesel", "diesel": "diesel",
    "汽油": "petrol", "petrol": "petrol", "gasoline": "petrol",
    "unleaded": "petrol",
}

# ---------------------------------------------------------------------------
# Pattern banks
# ---------------------------------------------------------------------------

_WEATHER_RE = re.compile(
    r"天气|气温|温度|预报|下雨|降雨|晴|阴|多云|"
    r"weather|forecast|temperature|rain|sunny|cloud|humid",
    re.IGNORECASE,
)

_TIME_RE = re.compile(
    r"几点|现在几点|时间|时刻|what time|current time|\btime\b|\bnow\b",
    re.IGNORECASE,
)

_CRYPTO_RE = re.compile(
    r"比特币|以太坊|狗狗币|加密货币|虚拟货币|数字货币|"
    r"bitcoin|ethereum|crypto|coin price|币价",
    re.IGNORECASE,
)

_STOCK_RE = re.compile(
    r"股票|股价|大盘|股市|stock price|share price|股市行情",
    re.IGNORECASE,
)

_EXCHANGE_RE = re.compile(
    r"汇率|兑换|外汇|exchange rate|convert currency",
    re.IGNORECASE,
)

_FUEL_RE = re.compile(
    r"油价|汽油|柴油|加油|油站|petrol|diesel|fuel price|gas price",
    re.IGNORECASE,
)

_SPORTS_RE = re.compile(
    r"比分|赛果|比赛|得分|积分|score|result|match|game result",
    re.IGNORECASE,
)

_COUNTDOWN_RE = re.compile(
    r"倒计时|还有几天|距离|剩余天数|countdown|days until|days left",
    re.IGNORECASE,
)


class QueryAnalyzer:
    """Analyze a user query and return a RealtimeIntent."""

    def analyze(self, query: str) -> RealtimeIntent:
        """Main entry point.  Returns RealtimeIntent (never raises)."""
        try:
            return self._analyze(query)
        except Exception as exc:
            logger.exception("query_analyzer_error query=%s error=%s", query[:50], exc)
            return RealtimeIntent(subtype="unknown", confidence=0.0, raw_query=query)

    def _analyze(self, query: str) -> RealtimeIntent:
        q_low = query.lower()

        # Score each subtype
        scores: dict[str, float] = {
            "weather":   self._score_weather(q_low),
            "time":      self._score_time(q_low),
            "crypto":    self._score_crypto(q_low),
            "stock":     self._score_stock(q_low),
            "exchange":  self._score_exchange(q_low),
            "fuel":      self._score_fuel(q_low),
            "sports":    self._score_sports(q_low),
            "countdown": self._score_countdown(q_low),
        }

        best = max(scores, key=lambda k: scores[k])
        score = scores[best]

        if score < 0.3:
            return RealtimeIntent(subtype="unknown", confidence=score, raw_query=query)

        entities = self._extract_entities(query, q_low, best)
        logger.info(
            "query_analyzed subtype=%s confidence=%.2f entities=%s",
            best, min(score, 1.0), entities,
        )
        return RealtimeIntent(
            subtype=best,  # type: ignore[arg-type]
            confidence=min(score, 1.0),
            entities=entities,
            raw_query=query,
        )

    # ------------------------------------------------------------------
    # Scoring functions
    # ------------------------------------------------------------------

    @staticmethod
    def _score_weather(q: str) -> float:
        base = 0.6 if _WEATHER_RE.search(q) else 0.0
        # Boost if city name also present
        if base and any(loc.lower() in q for loc in _LOCATIONS):
            base += 0.3
        return base

    @staticmethod
    def _score_time(q: str) -> float:
        base = 0.7 if _TIME_RE.search(q) else 0.0
        if base and any(loc.lower() in q for loc in _LOCATIONS):
            base += 0.25
        return base

    @staticmethod
    def _score_crypto(q: str) -> float:
        if _CRYPTO_RE.search(q):
            # Check for known symbol or name
            q_low = q
            for name in _CRYPTO_SYMBOLS:
                if name in q_low:
                    return 0.95
            return 0.7
        return 0.0

    @staticmethod
    def _score_stock(q: str) -> float:
        if _STOCK_RE.search(q):
            q_low = q
            for name in _STOCK_SYMBOLS:
                if name in q_low:
                    return 0.95
            # bare ticker pattern like NVDA, AAPL
            if re.search(r'\b[A-Z]{2,5}\b', q):
                return 0.80
            return 0.70
        # Check for known company/ticker without explicit "stock" keyword
        for name in _STOCK_SYMBOLS:
            if name in q:
                return 0.75
        return 0.0

    @staticmethod
    def _score_exchange(q: str) -> float:
        if _EXCHANGE_RE.search(q):
            return 0.85
        # Two currency mentions implies exchange
        found = []
        q_low = q
        for name in _CURRENCY_CODES:
            if name in q_low:
                found.append(name)
        if len(found) >= 2:
            return 0.80
        return 0.0

    @staticmethod
    def _score_fuel(q: str) -> float:
        return 0.85 if _FUEL_RE.search(q) else 0.0

    @staticmethod
    def _score_sports(q: str) -> float:
        return 0.80 if _SPORTS_RE.search(q) else 0.0

    @staticmethod
    def _score_countdown(q: str) -> float:
        return 0.80 if _COUNTDOWN_RE.search(q) else 0.0

    # ------------------------------------------------------------------
    # Entity extraction
    # ------------------------------------------------------------------

    def _extract_entities(self, query: str, q_low: str, subtype: str) -> dict[str, str]:
        entities: dict[str, str] = {}

        if subtype in ("weather", "time", "fuel"):
            loc = self._extract_location(query, q_low)
            if loc:
                entities["location"] = loc

        if subtype == "fuel":
            ft = self._extract_fuel_type(q_low)
            if ft:
                entities["fuel_type"] = ft

        if subtype == "crypto":
            sym = self._extract_crypto(q_low)
            if sym:
                entities["symbol"] = sym

        if subtype == "stock":
            sym = self._extract_stock(query, q_low)
            if sym:
                entities["symbol"] = sym

        if subtype == "exchange":
            pair = self._extract_currency_pair(q_low)
            if pair:
                entities["from_currency"] = pair[0]
                entities["to_currency"] = pair[1]

        if subtype == "sports":
            sport = self._extract_sport(q_low)
            if sport:
                entities["event"] = sport

        return entities

    @staticmethod
    def _extract_location(query: str, q_low: str) -> str | None:
        for loc in _LOCATIONS:  # already sorted longest-first
            if loc.lower() in q_low:
                return loc
        # Fallback: first capitalized sequence
        m = re.search(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b', query)
        if m:
            candidate = m.group(1)
            # Filter out generic sentence starts that aren't locations
            if len(candidate) > 2 and candidate not in (
                "What", "How", "When", "Where", "Does", "Is", "Are",
                "The", "Please", "Help", "Tell", "Show",
            ):
                return candidate
        return None

    @staticmethod
    def _extract_fuel_type(q_low: str) -> str | None:
        for name, code in _FUEL_TYPES.items():
            if name.lower() in q_low:
                return code
        return None

    @staticmethod
    def _extract_crypto(q_low: str) -> str | None:
        for name, sym in _CRYPTO_SYMBOLS.items():
            if name in q_low:
                return sym
        return None

    @staticmethod
    def _extract_stock(query: str, q_low: str) -> str | None:
        for name, sym in _STOCK_SYMBOLS.items():
            if name in q_low:
                return sym
        # bare uppercase ticker
        m = re.search(r'\b([A-Z]{2,5})\b', query)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _extract_currency_pair(q_low: str) -> tuple[str, str] | None:
        found: list[str] = []
        for name, code in _CURRENCY_CODES.items():
            if name in q_low and code not in found:
                found.append(code)
        if len(found) >= 2:
            return (found[0], found[1])
        return None

    @staticmethod
    def _extract_sport(q_low: str) -> str | None:
        sports = [
            "NBA", "英超", "欧冠", "世界杯", "Premier League",
            "篮球", "足球", "棒球", "冰球", "网球",
            "Lakers", "湖人", "Celtics",
        ]
        for s in sports:
            if s.lower() in q_low:
                return s
        return None
