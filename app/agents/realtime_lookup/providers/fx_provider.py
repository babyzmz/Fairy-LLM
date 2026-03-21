"""FX / exchange-rate provider using exchangerate.host (free, no key).

Stage-1 structured provider. Returns numeric data only.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Alias map for display names → ISO codes
_ALIAS_MAP: dict[str, str] = {
    "美元": "USD", "dollar": "USD", "usd": "USD",
    "欧元": "EUR", "euro": "EUR",   "eur": "EUR",
    "英镑": "GBP", "pound": "GBP", "gbp": "GBP",
    "人民币": "CNY", "rmb": "CNY",  "cny": "CNY",
    "日元": "JPY", "yen": "JPY",   "jpy": "JPY",
    "澳元": "AUD", "australian": "AUD", "aud": "AUD",
    "加元": "CAD", "canadian": "CAD",  "cad": "CAD",
    "港元": "HKD", "hkd": "HKD",
    "新元": "SGD", "sgd": "SGD",
    "韩元": "KRW", "krw": "KRW",
    "瑞士法郎": "CHF", "chf": "CHF",
    "泰铢": "THB", "thb": "THB",
    "新台币": "TWD", "twd": "TWD",
    "nzd": "NZD", "纽元": "NZD",
}


class FxProvider:
    """Fetch live exchange rate from exchangerate.host (or fallback to frankfurter.app)."""

    PRIMARY_URL  = "https://open.er-api.com/v6/latest/{base}"
    FALLBACK_URL = "https://api.frankfurter.app/latest"
    TIMEOUT = 8

    @classmethod
    def fetch(cls, from_currency: str, to_currency: str) -> dict[str, Any] | None:
        """Fetch exchange rate from *from_currency* to *to_currency*.

        Accepts ISO codes (USD, AUD) or display names (美元, Australian).

        Returns:
            dict with keys: from_currency, to_currency, rate
            or None on failure.
        """
        if requests is None:
            logger.error("provider_fx_no_requests")
            return None

        base   = cls._normalize(from_currency)
        target = cls._normalize(to_currency)
        logger.info("provider_fx_fetching base=%s target=%s", base, target)

        # Primary: open.er-api.com
        rate = cls._fetch_primary(base, target)
        if rate is not None:
            logger.info("provider_fx_success base=%s target=%s rate=%s", base, target, rate)
            return {"from_currency": base, "to_currency": target, "rate": rate}

        # Fallback: frankfurter.app
        rate = cls._fetch_fallback(base, target)
        if rate is not None:
            logger.info("provider_fx_fallback_success base=%s target=%s rate=%s", base, target, rate)
            return {"from_currency": base, "to_currency": target, "rate": rate}

        logger.warning("provider_fx_failed base=%s target=%s", base, target)
        return None

    @classmethod
    def _fetch_primary(cls, base: str, target: str) -> float | None:
        try:
            resp = requests.get(
                cls.PRIMARY_URL.format(base=base),
                timeout=cls.TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            rates = data.get("rates", {})
            return rates.get(target)
        except Exception as exc:
            logger.debug("provider_fx_primary_error error=%s", exc)
            return None

    @classmethod
    def _fetch_fallback(cls, base: str, target: str) -> float | None:
        try:
            resp = requests.get(
                cls.FALLBACK_URL,
                params={"from": base, "to": target},
                timeout=cls.TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            rates = data.get("rates", {})
            val = rates.get(target)
            return float(val) if val is not None else None
        except Exception as exc:
            logger.debug("provider_fx_fallback_error error=%s", exc)
            return None

    @staticmethod
    def _normalize(currency: str) -> str:
        """Normalize currency string to ISO 4217 code."""
        lower = currency.lower().strip()
        if lower in _ALIAS_MAP:
            return _ALIAS_MAP[lower]
        return currency.upper().strip()
