from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest
from fairy_core.providers import SecretValue

from fairy_capabilities.information.alpha_vantage import AlphaVantageAdapter
from fairy_capabilities.information.http import (
    CapabilityUnavailableError,
    InformationProviderError,
)


def test_stock_quote_is_normalized_and_explicitly_labelled_delayed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["function"] == "GLOBAL_QUOTE"
        assert request.url.params["symbol"] == "MSFT"
        assert request.url.params["apikey"] == "fixture-alpha-secret"
        return httpx.Response(
            200,
            json={
                "Global Quote": {
                    "01. symbol": "MSFT",
                    "05. price": "496.6200",
                    "08. previous close": "497.4500",
                    "09. change": "-0.8300",
                    "10. change percent": "-0.1669%",
                    "07. latest trading day": "2026-07-10",
                }
            },
        )

    adapter = AlphaVantageAdapter(
        secret=SecretValue.from_text("fixture-alpha-secret"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: datetime(2026, 7, 11, tzinfo=UTC),
    )

    result = adapter.stock("msft")

    assert result.symbol == "MSFT"
    assert result.price == 496.62
    assert result.previous_close == 497.45
    assert result.change_percent == -0.1669
    assert result.trading_day == date(2026, 7, 10)
    assert result.delayed is True
    assert result.freshness == "delayed_or_last_close_2026-07-10"
    assert "fixture-alpha-secret" not in result.source_url


def test_crypto_daily_quote_uses_requested_market_currency() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["function"] == "DIGITAL_CURRENCY_DAILY"
        assert request.url.params["symbol"] == "BTC"
        assert request.url.params["market"] == "AUD"
        return httpx.Response(
            200,
            json={
                "Meta Data": {
                    "2. Digital Currency Code": "BTC",
                    "4. Market Code": "AUD",
                    "6. Last Refreshed": "2026-07-10 00:00:00",
                },
                "Time Series (Digital Currency Daily)": {
                    "2026-07-10": {
                        "1a. open (AUD)": "166000.0",
                        "2a. high (AUD)": "169500.0",
                        "3a. low (AUD)": "165000.0",
                        "4a. close (AUD)": "168250.5",
                        "5. volume": "1234.5",
                    }
                },
            },
        )

    adapter = AlphaVantageAdapter(
        secret=SecretValue.from_text("fixture-alpha-secret"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = adapter.crypto(symbol="btc", market="aud")

    assert result.symbol == "BTC"
    assert result.market_currency == "AUD"
    assert result.close == 168250.5
    assert result.volume == 1234.5
    assert result.trading_day == date(2026, 7, 10)
    assert result.freshness == "daily_close_2026-07-10"


def test_missing_key_is_unavailable_without_network_request() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    adapter = AlphaVantageAdapter(
        secret=None,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    health = adapter.health()

    with pytest.raises(CapabilityUnavailableError) as captured:
        adapter.stock("MSFT")

    assert health.status == "unavailable"
    assert health.error_code == "CAPABILITY_NOT_AVAILABLE"
    assert captured.value.error_code == "CAPABILITY_NOT_AVAILABLE"
    assert called is False


@pytest.mark.parametrize("payload", [{"Note": "fixture-alpha-secret"}, {"Information": "limit"}])
def test_rate_limit_payload_is_sanitized(payload: dict[str, str]) -> None:
    adapter = AlphaVantageAdapter(
        secret=SecretValue.from_text("fixture-alpha-secret"),
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
        ),
    )

    with pytest.raises(InformationProviderError) as captured:
        adapter.stock("MSFT")

    assert captured.value.error_code == "RATE_LIMITED"
    assert "fixture-alpha-secret" not in str(captured.value)
