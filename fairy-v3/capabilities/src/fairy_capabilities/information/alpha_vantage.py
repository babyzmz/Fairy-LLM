from __future__ import annotations

import re
import time
from datetime import UTC, date, datetime
from typing import Any

import httpx
from fairy_core.information import (
    CryptoResult,
    InformationCapabilityHealth,
    InformationCapabilityStatus,
    StockResult,
)
from fairy_core.providers import SecretValue

from fairy_capabilities.information.http import (
    BoundedJsonClient,
    CapabilityUnavailableError,
    InformationProviderError,
)

_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,15}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class AlphaVantageAdapter:
    def __init__(
        self,
        *,
        secret: SecretValue | None,
        client: httpx.Client | None = None,
        endpoint: str = "https://www.alphavantage.co/query",
        clock=lambda: datetime.now(UTC),
        timeout_seconds: float = 10,
        max_response_bytes: int = 2 * 1024 * 1024,
        retries: int = 1,
        sleeper=time.sleep,
    ) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("Alpha Vantage endpoint must use HTTPS")
        self._secret = secret
        self._endpoint = endpoint
        self._clock = clock
        self._http = BoundedJsonClient(
            client=client,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            retries=retries,
            sleeper=sleeper,
        )

    def close(self) -> None:
        self._http.close()

    def health(self) -> InformationCapabilityHealth:
        available = self._secret is not None
        return InformationCapabilityHealth(
            provider="alpha_vantage",
            status=(
                InformationCapabilityStatus.AVAILABLE
                if available
                else InformationCapabilityStatus.UNAVAILABLE
            ),
            observed_at=self._clock(),
            error_code=None if available else "CAPABILITY_NOT_AVAILABLE",
            diagnostics=(),
        )

    def stock(self, symbol: str) -> StockResult:
        normalized_symbol = _symbol(symbol)
        payload = self._request(
            {
                "function": "GLOBAL_QUOTE",
                "symbol": normalized_symbol,
            }
        )
        try:
            quote = payload["Global Quote"]
            if not isinstance(quote, dict):
                raise TypeError("quote")
            response_symbol = str(quote["01. symbol"]).strip().upper()
            if response_symbol != normalized_symbol:
                raise ValueError("symbol mismatch")
            trading_day = date.fromisoformat(str(quote["07. latest trading day"]))
            return StockResult(
                provider="alpha_vantage",
                observed_at=self._clock(),
                freshness=f"delayed_or_last_close_{trading_day.isoformat()}",
                source_url="https://www.alphavantage.co/documentation/",
                diagnostics=("quote_policy=delayed_or_last_close",),
                symbol=normalized_symbol,
                currency=None,
                price=float(quote["05. price"]),
                previous_close=float(quote["08. previous close"]),
                change=float(quote["09. change"]),
                change_percent=_percent(quote["10. change percent"]),
                trading_day=trading_day,
                delayed=True,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise InformationProviderError(
                "PROTOCOL_ERROR",
                "Alpha Vantage stock quote returned an invalid payload",
            ) from error

    def crypto(self, *, symbol: str, market: str) -> CryptoResult:
        normalized_symbol = _symbol(symbol)
        market_currency = _currency(market)
        payload = self._request(
            {
                "function": "DIGITAL_CURRENCY_DAILY",
                "symbol": normalized_symbol,
                "market": market_currency,
            }
        )
        try:
            metadata = payload["Meta Data"]
            series = payload["Time Series (Digital Currency Daily)"]
            if not isinstance(metadata, dict) or not isinstance(series, dict) or not series:
                raise TypeError("series")
            if str(metadata["2. Digital Currency Code"]).upper() != normalized_symbol:
                raise ValueError("symbol mismatch")
            if str(metadata["4. Market Code"]).upper() != market_currency:
                raise ValueError("market mismatch")
            latest_key = max(str(value) for value in series)
            values = series[latest_key]
            if not isinstance(values, dict):
                raise TypeError("daily value")
            trading_day = date.fromisoformat(latest_key)
            close = _market_value(values, prefix="4", market=market_currency)
            volume = float(values["5. volume"])
            return CryptoResult(
                provider="alpha_vantage",
                observed_at=self._clock(),
                freshness=f"daily_close_{trading_day.isoformat()}",
                source_url="https://www.alphavantage.co/documentation/",
                diagnostics=("series=digital_currency_daily",),
                symbol=normalized_symbol,
                market_currency=market_currency,
                close=close,
                volume=volume,
                trading_day=trading_day,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise InformationProviderError(
                "PROTOCOL_ERROR",
                "Alpha Vantage crypto quote returned an invalid payload",
            ) from error

    def _request(self, params: dict[str, str]) -> dict[str, Any]:
        if self._secret is None:
            raise CapabilityUnavailableError("Alpha Vantage")
        payload = self._http.get_json(
            self._endpoint,
            params={**params, "apikey": self._secret.reveal()},
        )
        if not isinstance(payload, dict):
            raise InformationProviderError(
                "PROTOCOL_ERROR",
                "Alpha Vantage returned an invalid payload",
            )
        if "Note" in payload or "Information" in payload:
            raise InformationProviderError(
                "RATE_LIMITED",
                "Alpha Vantage rate limit was reached",
            )
        if "Error Message" in payload:
            raise InformationProviderError(
                "REQUEST_REJECTED",
                "Alpha Vantage rejected the request",
            )
        return payload


def _symbol(value: str) -> str:
    normalized = value.strip().upper()
    if _SYMBOL.fullmatch(normalized) is None:
        raise ValueError("symbol is invalid")
    return normalized


def _currency(value: str) -> str:
    normalized = value.strip().upper()
    if _CURRENCY.fullmatch(normalized) is None:
        raise ValueError("market currency must be a three-letter code")
    return normalized


def _percent(value: object) -> float:
    return float(str(value).strip().removesuffix("%"))


def _market_value(values: dict[str, Any], *, prefix: str, market: str) -> float:
    expected = f"({market})"
    matches = [value for key, value in values.items() if key.startswith(prefix) and expected in key]
    if len(matches) != 1:
        raise ValueError("market value is missing")
    return float(matches[0])


__all__ = ["AlphaVantageAdapter"]
