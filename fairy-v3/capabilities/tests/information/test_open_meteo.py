from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from fairy_capabilities.information.open_meteo import (
    AmbiguousLocationError,
    InformationProviderError,
    OpenMeteoAdapter,
)


def _location(name: str, country_code: str, latitude: float, longitude: float) -> dict:
    return {
        "id": int(abs(latitude) * 1000),
        "name": name,
        "country": "Australia" if country_code == "AU" else "United States",
        "country_code": country_code,
        "admin1": "New South Wales" if country_code == "AU" else "Illinois",
        "latitude": latitude,
        "longitude": longitude,
        "timezone": "Australia/Sydney" if country_code == "AU" else "America/Chicago",
    }


def test_geocoding_ambiguity_stops_before_weather_request() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "results": [
                    _location("Springfield", "US", 39.78, -89.64),
                    _location("Springfield", "AU", -27.67, 152.90),
                ]
            },
        )

    adapter = OpenMeteoAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(AmbiguousLocationError) as captured:
        adapter.current_weather(location="Springfield", units="metric")

    assert [candidate.country_code for candidate in captured.value.candidates] == ["US", "AU"]
    assert paths == ["/v1/search"]


def test_weather_uses_explicit_units_and_normalizes_current_values() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/search":
            assert request.url.params["name"] == "Sydney"
            assert request.url.params["countryCode"] == "AU"
            return httpx.Response(
                200,
                json={"results": [_location("Sydney", "AU", -33.8688, 151.2093)]},
            )
        assert request.url.path == "/v1/forecast"
        assert request.url.params["temperature_unit"] == "fahrenheit"
        assert request.url.params["wind_speed_unit"] == "mph"
        assert request.url.params["precipitation_unit"] == "inch"
        assert request.url.params["timezone"] == "Australia/Sydney"
        assert request.url.params["current"] == (
            "temperature_2m,apparent_temperature,relative_humidity_2m,"
            "precipitation,weather_code,wind_speed_10m"
        )
        return httpx.Response(
            200,
            json={
                "latitude": -33.87,
                "longitude": 151.21,
                "timezone": "Australia/Sydney",
                "current": {
                    "time": "2026-07-11T14:15",
                    "interval": 900,
                    "temperature_2m": 61.7,
                    "apparent_temperature": 60.2,
                    "relative_humidity_2m": 72,
                    "precipitation": 0.01,
                    "weather_code": 3,
                    "wind_speed_10m": 8.4,
                },
                "current_units": {
                    "temperature_2m": "°F",
                    "apparent_temperature": "°F",
                    "relative_humidity_2m": "%",
                    "precipitation": "inch",
                    "wind_speed_10m": "mp/h",
                },
            },
        )

    adapter = OpenMeteoAdapter(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: datetime(2026, 7, 11, 4, 16, tzinfo=UTC),
    )

    result = adapter.current_weather(
        location="Sydney",
        country_code="au",
        units="imperial",
    )

    assert result.provider == "open_meteo"
    assert result.location.country_code == "AU"
    assert result.temperature == 61.7
    assert result.temperature_unit == "°F"
    assert result.wind_speed_unit == "mp/h"
    assert result.data_time.isoformat() == "2026-07-11T14:15:00+10:00"
    assert result.freshness == "current_15_minute_model"
    assert "apikey" not in result.source_url


def test_weather_retries_one_idempotent_server_failure_then_succeeds() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        if request.url.path == "/v1/search":
            return httpx.Response(
                200,
                json={"results": [_location("Sydney", "AU", -33.8688, 151.2093)]},
            )
        return httpx.Response(
            200,
            json={
                "timezone": "Australia/Sydney",
                "current": {
                    "time": "2026-07-11T14:15",
                    "temperature_2m": 16.5,
                    "apparent_temperature": 15.6,
                    "relative_humidity_2m": 72,
                    "precipitation": 0.0,
                    "weather_code": 3,
                    "wind_speed_10m": 12.0,
                },
                "current_units": {
                    "temperature_2m": "°C",
                    "apparent_temperature": "°C",
                    "relative_humidity_2m": "%",
                    "precipitation": "mm",
                    "wind_speed_10m": "km/h",
                },
            },
        )

    adapter = OpenMeteoAdapter(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retries=1,
        sleeper=lambda _seconds: None,
    )

    result = adapter.current_weather(location="Sydney", units="metric")

    assert result.temperature == 16.5
    assert calls == 3


def test_weather_rejects_provider_error_payload_without_leaking_details() -> None:
    adapter = OpenMeteoAdapter(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    400,
                    json={"error": True, "reason": "private fixture details"},
                )
            )
        )
    )

    with pytest.raises(InformationProviderError) as captured:
        adapter.current_weather(location="Sydney", units="metric")

    assert captured.value.error_code == "REQUEST_REJECTED"
    assert "private fixture details" not in str(captured.value)
