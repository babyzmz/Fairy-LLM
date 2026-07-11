from __future__ import annotations

import math
import re
import time
from datetime import UTC, date, datetime

import httpx
from fairy_core.information import (
    FxResult,
    InformationCapabilityHealth,
    InformationCapabilityStatus,
)

from fairy_capabilities.information.http import BoundedJsonClient, InformationProviderError

_CURRENCY = re.compile(r"^[A-Z]{3}$")


class FrankfurterAdapter:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        endpoint: str = "https://api.frankfurter.dev/v2",
        clock=lambda: datetime.now(UTC),
        timeout_seconds: float = 10,
        max_response_bytes: int = 256 * 1024,
        retries: int = 1,
        sleeper=time.sleep,
    ) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("Frankfurter endpoint must use HTTPS")
        self._endpoint = endpoint.rstrip("/")
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
        return InformationCapabilityHealth(
            provider="frankfurter_v2",
            status=InformationCapabilityStatus.AVAILABLE,
            observed_at=self._clock(),
            error_code=None,
            diagnostics=("api_version=v2",),
        )

    def convert(self, *, base: str, quote: str, amount: float) -> FxResult:
        base_currency = _currency(base)
        quote_currency = _currency(quote)
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise ValueError("amount must be numeric")
        normalized_amount = float(amount)
        if not math.isfinite(normalized_amount) or not 0 <= normalized_amount <= 1e15:
            raise ValueError("amount is outside the supported range")
        source_url = f"{self._endpoint}/rate/{base_currency}/{quote_currency}"
        payload = self._http.get_json(source_url, params={})
        try:
            if not isinstance(payload, dict):
                raise TypeError("payload")
            response_base = str(payload["base"]).upper()
            response_quote = str(payload["quote"]).upper()
            if response_base != base_currency or response_quote != quote_currency:
                raise ValueError("currency pair mismatch")
            rate = float(payload["rate"])
            rate_date = date.fromisoformat(str(payload["date"]))
            observed_at = self._clock()
            return FxResult(
                provider="frankfurter_v2",
                observed_at=observed_at,
                freshness=f"reference_rate_{rate_date.isoformat()}",
                source_url=source_url,
                diagnostics=("rate_type=central_bank_reference",),
                base_currency=base_currency,
                quote_currency=quote_currency,
                amount=normalized_amount,
                rate=rate,
                converted_amount=normalized_amount * rate,
                rate_date=rate_date,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise InformationProviderError(
                "PROTOCOL_ERROR",
                "Frankfurter returned an invalid payload",
            ) from error


def _currency(value: str) -> str:
    normalized = value.strip().upper()
    if _CURRENCY.fullmatch(normalized) is None:
        raise ValueError("currency must be a three-letter code")
    return normalized


__all__ = ["FrankfurterAdapter"]
