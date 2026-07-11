from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fairy_core.information import (
    InformationCapabilityHealth,
    InformationCapabilityStatus,
    LocationCandidate,
    UnitSystem,
    WeatherResult,
)

from fairy_capabilities.information.http import (
    BoundedJsonClient,
    InformationProviderError,
)

_CURRENT_FIELDS = (
    "temperature_2m,apparent_temperature,relative_humidity_2m,"
    "precipitation,weather_code,wind_speed_10m"
)


class AmbiguousLocationError(InformationProviderError):
    def __init__(self, candidates: tuple[LocationCandidate, ...]) -> None:
        self.candidates = candidates
        super().__init__(
            "LOCATION_AMBIGUOUS",
            "location matched multiple candidates",
        )


class OpenMeteoAdapter:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        geocoding_endpoint: str = "https://geocoding-api.open-meteo.com/v1/search",
        forecast_endpoint: str = "https://api.open-meteo.com/v1/forecast",
        clock=lambda: datetime.now(UTC),
        timeout_seconds: float = 10,
        max_response_bytes: int = 2 * 1024 * 1024,
        retries: int = 1,
        sleeper=time.sleep,
    ) -> None:
        if not geocoding_endpoint.startswith("https://") or not forecast_endpoint.startswith(
            "https://"
        ):
            raise ValueError("Open-Meteo endpoints must use HTTPS")
        self._http = BoundedJsonClient(
            client=client,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            retries=retries,
            sleeper=sleeper,
        )
        self._geocoding_endpoint = geocoding_endpoint
        self._forecast_endpoint = forecast_endpoint
        self._clock = clock

    def close(self) -> None:
        self._http.close()

    def health(self) -> InformationCapabilityHealth:
        return InformationCapabilityHealth(
            provider="open_meteo",
            status=InformationCapabilityStatus.AVAILABLE,
            observed_at=_aware_clock(self._clock()),
            error_code=None,
            diagnostics=(),
        )

    def geocode(
        self,
        location: str,
        *,
        country_code: str | None = None,
    ) -> tuple[LocationCandidate, ...]:
        query = _required_text(location, "location", maximum=500)
        if len(query) < 2:
            raise ValueError("location must contain at least two characters")
        country = _country_code(country_code)
        params: dict[str, str | int] = {
            "name": query,
            "count": 5,
            "format": "json",
            "language": "en",
        }
        if country is not None:
            params["countryCode"] = country
        payload = self._http.get_json(self._geocoding_endpoint, params=params)
        try:
            if not isinstance(payload, dict):
                raise TypeError("payload")
            values = payload.get("results", [])
            if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
                raise TypeError("results")
            candidates = tuple(_location_candidate(item) for item in values[:5])
        except (KeyError, TypeError, ValueError) as error:
            raise InformationProviderError(
                "PROTOCOL_ERROR",
                "Open-Meteo geocoding returned an invalid payload",
            ) from error
        if not candidates:
            raise InformationProviderError(
                "LOCATION_NOT_FOUND",
                "location was not found",
            )
        return candidates

    def current_weather(
        self,
        *,
        location: str,
        units: UnitSystem | str,
        country_code: str | None = None,
        candidate_index: int | None = None,
    ) -> WeatherResult:
        unit_system = UnitSystem(units)
        candidates = self.geocode(location, country_code=country_code)
        if candidate_index is None:
            if len(candidates) != 1:
                raise AmbiguousLocationError(candidates)
            selected = candidates[0]
        else:
            if isinstance(candidate_index, bool) or not 1 <= candidate_index <= len(candidates):
                raise ValueError("candidate_index is outside the geocoding result range")
            selected = candidates[candidate_index - 1]
        unit_params = (
            {
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "precipitation_unit": "inch",
            }
            if unit_system is UnitSystem.IMPERIAL
            else {
                "temperature_unit": "celsius",
                "wind_speed_unit": "kmh",
                "precipitation_unit": "mm",
            }
        )
        params: dict[str, str | int | float] = {
            "latitude": selected.latitude,
            "longitude": selected.longitude,
            "current": _CURRENT_FIELDS,
            "timezone": selected.timezone,
            **unit_params,
        }
        payload = self._http.get_json(self._forecast_endpoint, params=params)
        source_url = str(httpx.URL(self._forecast_endpoint, params=params))
        observed_at = _aware_clock(self._clock())
        try:
            if not isinstance(payload, dict):
                raise TypeError("payload")
            current = payload["current"]
            current_units = payload["current_units"]
            if not isinstance(current, dict) or not isinstance(current_units, dict):
                raise TypeError("current")
            timezone_name = str(payload.get("timezone", selected.timezone))
            data_time = datetime.fromisoformat(str(current["time"]))
            data_time = data_time.replace(tzinfo=ZoneInfo(timezone_name))
            return WeatherResult(
                provider="open_meteo",
                observed_at=observed_at,
                freshness="current_15_minute_model",
                source_url=source_url,
                diagnostics=(f"geocoding_candidates={len(candidates)}",),
                location=selected,
                unit_system=unit_system,
                data_time=data_time,
                temperature=float(current["temperature_2m"]),
                temperature_unit=str(current_units["temperature_2m"]),
                apparent_temperature=float(current["apparent_temperature"]),
                relative_humidity=int(current["relative_humidity_2m"]),
                precipitation=float(current["precipitation"]),
                precipitation_unit=str(current_units["precipitation"]),
                weather_code=int(current["weather_code"]),
                wind_speed=float(current["wind_speed_10m"]),
                wind_speed_unit=str(current_units["wind_speed_10m"]),
            )
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError) as error:
            raise InformationProviderError(
                "PROTOCOL_ERROR",
                "Open-Meteo weather returned an invalid payload",
            ) from error


def _location_candidate(value: dict[str, Any]) -> LocationCandidate:
    return LocationCandidate(
        provider_id=int(value["id"]),
        name=str(value["name"]).strip(),
        country=str(value["country"]).strip(),
        country_code=str(value["country_code"]).strip().upper(),
        admin1=str(value["admin1"]).strip() if value.get("admin1") else None,
        latitude=float(value["latitude"]),
        longitude=float(value["longitude"]),
        timezone=str(value["timezone"]).strip(),
    )


def _country_code(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if len(normalized) != 2 or not normalized.isalpha():
        raise ValueError("country_code must be ISO alpha-2")
    return normalized


def _required_text(value: str, name: str, *, maximum: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} is invalid")
    return normalized


def _aware_clock(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("information clock must be timezone-aware")
    return value


__all__ = [
    "AmbiguousLocationError",
    "InformationProviderError",
    "OpenMeteoAdapter",
]
