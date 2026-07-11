from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from fairy_capabilities.information.frankfurter import FrankfurterAdapter
from fairy_capabilities.information.http import InformationProviderError


def test_fx_uses_current_v2_pair_endpoint_and_retains_reference_date() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/rate/USD/EUR"
        return httpx.Response(
            200,
            json={"date": "2026-07-10", "base": "USD", "quote": "EUR", "rate": 0.8754},
        )

    adapter = FrankfurterAdapter(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: datetime(2026, 7, 11, tzinfo=UTC),
    )

    result = adapter.convert(base="usd", quote="eur", amount=125.5)

    assert result.base_currency == "USD"
    assert result.quote_currency == "EUR"
    assert result.rate == 0.8754
    assert result.amount == 125.5
    assert result.converted_amount == pytest.approx(109.8627)
    assert result.rate_date == date(2026, 7, 10)
    assert result.freshness == "reference_rate_2026-07-10"
    assert result.provider == "frankfurter_v2"


def test_fx_rejects_invalid_currency_before_request() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    adapter = FrankfurterAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ValueError, match="currency"):
        adapter.convert(base="US D", quote="EUR", amount=1)

    assert called is False


def test_fx_maps_provider_error_to_stable_code() -> None:
    adapter = FrankfurterAdapter(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(404, json={"message": "private details"})
            )
        )
    )

    with pytest.raises(InformationProviderError) as captured:
        adapter.convert(base="USD", quote="ZZZ", amount=1)

    assert captured.value.error_code == "REQUEST_REJECTED"
    assert "private details" not in str(captured.value)
