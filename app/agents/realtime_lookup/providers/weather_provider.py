"""Weather provider using Open-Meteo (free, no API key).

Stage-1 structured provider for weather queries.
Returns numeric data only; no LLM involvement.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Geocoding cache so we don't hit the API twice for the same city
_GEO_CACHE: dict[str, tuple[float, float]] = {}

# Pre-seeded coordinates for common cities
_KNOWN_COORDS: dict[str, tuple[float, float]] = {
    "Melbourne":    (-37.8136,  144.9631),
    "墨尔本":        (-37.8136,  144.9631),
    "Sydney":       (-33.8688,  151.2093),
    "悉尼":          (-33.8688,  151.2093),
    "Brisbane":     (-27.4698,  153.0251),
    "Perth":        (-31.9505,  115.8605),
    "Adelaide":     (-34.9285,  138.6007),
    "Tokyo":        ( 35.6762,  139.6503),
    "东京":          ( 35.6762,  139.6503),
    "London":       ( 51.5074,   -0.1278),
    "伦敦":          ( 51.5074,   -0.1278),
    "New York":     ( 40.7128,  -74.0060),
    "纽约":          ( 40.7128,  -74.0060),
    "Los Angeles":  ( 34.0522, -118.2437),
    "洛杉矶":        ( 34.0522, -118.2437),
    "Paris":        ( 48.8566,    2.3522),
    "巴黎":          ( 48.8566,    2.3522),
    "Beijing":      ( 39.9042,  116.4074),
    "北京":          ( 39.9042,  116.4074),
    "Shanghai":     ( 31.2304,  121.4737),
    "上海":          ( 31.2304,  121.4737),
    "Singapore":    (  1.3521,  103.8198),
    "新加坡":        (  1.3521,  103.8198),
    "Hong Kong":    ( 22.3193,  114.1694),
    "香港":          ( 22.3193,  114.1694),
    "Bangkok":      ( 13.7563,  100.5018),
    "曼谷":          ( 13.7563,  100.5018),
    "Dubai":        ( 25.2048,   55.2708),
    "迪拜":          ( 25.2048,   55.2708),
    "Forest Hill":  (-37.8258,  145.1778),
    "Canberra":     (-35.2809,  149.1300),
}

_WMO_CODES: dict[int, str] = {
    0:  "晴天 / Clear sky",
    1:  "基本晴天 / Mainly clear",
    2:  "局部多云 / Partly cloudy",
    3:  "阴天 / Overcast",
    45: "有雾 / Fog",
    48: "雾凇 / Rime fog",
    51: "小毛毛雨 / Light drizzle",
    61: "小雨 / Slight rain",
    63: "中雨 / Moderate rain",
    65: "大雨 / Heavy rain",
    71: "小雪 / Slight snow",
    73: "中雪 / Moderate snow",
    75: "大雪 / Heavy snow",
    80: "阵雨 / Rain showers",
    95: "雷暴 / Thunderstorm",
}


class WeatherProvider:
    """Fetch current weather via Open-Meteo (free, no key required)."""

    BASE_URL = "https://api.open-meteo.com/v1/forecast"
    GEO_URL  = "https://geocoding-api.open-meteo.com/v1/search"
    TIMEOUT  = 8

    @classmethod
    def fetch(cls, location: str) -> dict[str, Any] | None:
        """Fetch weather for *location*.

        Returns structured dict:
            temperature_c, feels_like_c, humidity_pct,
            wind_kmh, condition, condition_code
        or None on failure.
        """
        if requests is None:
            logger.error("provider_weather_no_requests")
            return None

        coords = cls._resolve_coords(location)
        if coords is None:
            logger.warning("provider_weather_no_coords location=%s", location)
            return None

        lat, lon = coords
        logger.info("provider_weather_fetching location=%s lat=%.4f lon=%.4f", location, lat, lon)

        params = {
            "latitude": lat,
            "longitude": lon,
            "current": [
                "temperature_2m",
                "relative_humidity_2m",
                "apparent_temperature",
                "weather_code",
                "wind_speed_10m",
            ],
            "forecast_days": 1,
        }
        try:
            resp = requests.get(cls.BASE_URL, params=params, timeout=cls.TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("provider_weather_api_error location=%s error=%s", location, exc)
            return None

        current = data.get("current", {})
        code = current.get("weather_code", 0)
        return {
            "temperature_c":  current.get("temperature_2m"),
            "feels_like_c":   current.get("apparent_temperature"),
            "humidity_pct":   current.get("relative_humidity_2m"),
            "wind_kmh":       current.get("wind_speed_10m"),
            "condition":      _WMO_CODES.get(code, "Unknown"),
            "condition_code": code,
        }

    @classmethod
    def _resolve_coords(cls, location: str) -> tuple[float, float] | None:
        """Resolve location name to (lat, lon)."""
        if location in _KNOWN_COORDS:
            return _KNOWN_COORDS[location]
        if location in _GEO_CACHE:
            return _GEO_CACHE[location]

        # Try geocoding API
        try:
            resp = requests.get(
                cls.GEO_URL,
                params={"name": location, "count": 1, "language": "en"},
                timeout=cls.TIMEOUT,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if results:
                lat = results[0]["latitude"]
                lon = results[0]["longitude"]
                _GEO_CACHE[location] = (lat, lon)
                return (lat, lon)
        except Exception as exc:
            logger.debug("provider_weather_geo_error location=%s error=%s", location, exc)

        return None
