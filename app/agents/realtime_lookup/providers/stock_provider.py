"""Stock price provider using Yahoo Finance JSON endpoint.

Stage-1 structured provider. Returns numeric data only.
No API key required.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Company name → ticker symbol
_NAME_MAP: dict[str, str] = {
    "nvidia":   "NVDA",
    "英伟达":     "NVDA",
    "apple":    "AAPL",
    "苹果":      "AAPL",
    "microsoft": "MSFT",
    "微软":      "MSFT",
    "tesla":    "TSLA",
    "特斯拉":     "TSLA",
    "google":   "GOOGL",
    "alphabet": "GOOGL",
    "谷歌":      "GOOGL",
    "amazon":   "AMZN",
    "亚马逊":     "AMZN",
    "meta":     "META",
    "facebook": "META",
    "netflix":  "NFLX",
}


class StockProvider:
    """Fetch live stock quote from Yahoo Finance (no key required)."""

    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    TIMEOUT  = 8
    HEADERS  = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }

    @classmethod
    def fetch(cls, symbol: str) -> dict[str, Any] | None:
        """Fetch stock quote for *symbol* (e.g. 'NVDA', 'AAPL').

        Also accepts company names like '英伟达' or 'nvidia'.

        Returns:
            dict with keys: symbol, price_usd, change_pct, company
            or None on failure.
        """
        if requests is None:
            logger.error("provider_stock_no_requests")
            return None

        ticker = cls._resolve_ticker(symbol)
        logger.info("provider_stock_fetching symbol=%s ticker=%s", symbol, ticker)

        url = cls.BASE_URL.format(ticker=ticker)
        try:
            resp = requests.get(
                url,
                params={"interval": "1d", "range": "1d"},
                headers=cls.HEADERS,
                timeout=cls.TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("provider_stock_api_error ticker=%s error=%s", ticker, exc)
            return None

        try:
            meta = data["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice")
            prev  = meta.get("chartPreviousClose") or meta.get("previousClose")
            company = meta.get("shortName", ticker)

            change_pct: float | None = None
            if price and prev and prev != 0:
                change_pct = round((price - prev) / prev * 100, 2)

            logger.info("provider_stock_success ticker=%s price=%s", ticker, price)
            return {
                "symbol":     ticker,
                "price_usd":  price,
                "change_pct": change_pct,
                "company":    company,
            }
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("provider_stock_parse_error ticker=%s error=%s", ticker, exc)
            return None

    @staticmethod
    def _resolve_ticker(raw: str) -> str:
        """Map company name or symbol to canonical ticker."""
        lower = raw.lower().strip()
        if lower in _NAME_MAP:
            return _NAME_MAP[lower]
        # Already a ticker-like string (uppercase letters)
        return raw.upper().strip()
