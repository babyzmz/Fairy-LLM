"""Crypto price provider using CoinGecko simple/price API (free, no key).

Stage-1 structured provider.  Returns numeric data only.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# CoinGecko ID map  symbol → coingecko_id
_SYMBOL_MAP: dict[str, str] = {
    "BTC":  "bitcoin",
    "ETH":  "ethereum",
    "DOGE": "dogecoin",
    "XRP":  "ripple",
    "SOL":  "solana",
    "ADA":  "cardano",
    "MATIC": "matic-network",
    "LTC":  "litecoin",
    "AVAX": "avalanche-2",
    "DOT":  "polkadot",
}


class CryptoProvider:
    """Fetch live crypto price from CoinGecko."""

    BASE_URL = "https://api.coingecko.com/api/v3/simple/price"
    TIMEOUT  = 8

    @classmethod
    def fetch(cls, symbol: str) -> dict[str, Any] | None:
        """Fetch price for *symbol* (e.g. 'BTC', 'ETH').

        Returns:
            dict with keys: symbol, price_usd, change_24h_pct
            or None on failure.
        """
        if requests is None:
            logger.error("provider_crypto_no_requests")
            return None

        coin_id = _SYMBOL_MAP.get(symbol.upper())
        if not coin_id:
            logger.warning("provider_crypto_unknown_symbol symbol=%s", symbol)
            return None

        logger.info("provider_crypto_fetching symbol=%s coin_id=%s", symbol, coin_id)
        try:
            resp = requests.get(
                cls.BASE_URL,
                params={
                    "ids": coin_id,
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                },
                timeout=cls.TIMEOUT,
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("provider_crypto_api_error symbol=%s error=%s", symbol, exc)
            return None

        coin_data = data.get(coin_id, {})
        if not coin_data:
            logger.warning("provider_crypto_empty_response symbol=%s", symbol)
            return None

        logger.info("provider_crypto_success symbol=%s price=%s", symbol, coin_data.get("usd"))
        return {
            "symbol":         symbol.upper(),
            "price_usd":      coin_data.get("usd"),
            "change_24h_pct": coin_data.get("usd_24h_change"),
        }
