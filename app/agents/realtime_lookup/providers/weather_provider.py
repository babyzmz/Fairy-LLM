"""Weather provider using Open-Meteo (free, no API key).

Stage-1 structured provider for weather queries.
Returns numeric data only; no LLM involvement.
"""

from __future__ import annotations

import logging
import re
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_GEO_CACHE: dict[str, tuple[float, float]] = {}

_LOCATION_ALIASES: dict[str, str] = {
    "new york city": "New York",
    "new york": "New York",
    "\u7ebd\u7ea6\u5e02": "\u7ebd\u7ea6",
    "\u6210\u90fd\u5e02": "\u6210\u90fd",
    "\u58a8\u5c14\u672c\u5e02": "\u58a8\u5c14\u672c",
    "\u963f\u5fb7\u83b1\u5fb7\u5e02": "\u963f\u5fb7\u83b1\u5fb7",
}

_KNOWN_COORDS: dict[str, tuple[float, float]] = {
    "Melbourne": (-37.8136, 144.9631),
    "\u58a8\u5c14\u672c": (-37.8136, 144.9631),
    "Sydney": (-33.8688, 151.2093),
    "\u6089\u5c3c": (-33.8688, 151.2093),
    "Brisbane": (-27.4698, 153.0251),
    "Perth": (-31.9505, 115.8605),
    "Adelaide": (-34.9285, 138.6007),
    "\u963f\u5fb7\u83b1\u5fb7": (-34.9285, 138.6007),
    "Canberra": (-35.2809, 149.1300),
    "Tokyo": (35.6762, 139.6503),
    "\u4e1c\u4eac": (35.6762, 139.6503),
    "London": (51.5074, -0.1278),
    "\u4f26\u6566": (51.5074, -0.1278),
    "New York": (40.7128, -74.0060),
    "\u7ebd\u7ea6": (40.7128, -74.0060),
    "Los Angeles": (34.0522, -118.2437),
    "\u6d1b\u6749\u77f6": (34.0522, -118.2437),
    "Paris": (48.8566, 2.3522),
    "\u5df4\u9ece": (48.8566, 2.3522),
    "Beijing": (39.9042, 116.4074),
    "\u5317\u4eac": (39.9042, 116.4074),
    "Shanghai": (31.2304, 121.4737),
    "\u4e0a\u6d77": (31.2304, 121.4737),
    "Chengdu": (30.5728, 104.0668),
    "\u6210\u90fd": (30.5728, 104.0668),
    "Singapore": (1.3521, 103.8198),
    "\u65b0\u52a0\u5761": (1.3521, 103.8198),
    "Hong Kong": (22.3193, 114.1694),
    "\u9999\u6e2f": (22.3193, 114.1694),
    "Bangkok": (13.7563, 100.5018),
    "\u66fc\u8c37": (13.7563, 100.5018),
    "Dubai": (25.2048, 55.2708),
    "\u8fea\u62dc": (25.2048, 55.2708),
    "Forest Hill": (-37.8258, 145.1778),
}

_WMO_CODES: dict[int, str] = {
    0: "\u6674\u5929 / Clear sky",
    1: "\u57fa\u672c\u6674\u5929 / Mainly clear",
    2: "\u5c40\u90e8\u591a\u4e91 / Partly cloudy",
    3: "\u9634\u5929 / Overcast",
    45: "\u6709\u96fe / Fog",
    48: "\u96fe\u51c7 / Rime fog",
    51: "\u5c0f\u6bdb\u6bdb\u96e8 / Light drizzle",
    61: "\u5c0f\u96e8 / Slight rain",
    63: "\u4e2d\u96e8 / Moderate rain",
    65: "\u5927\u96e8 / Heavy rain",
    71: "\u5c0f\u96ea / Slight snow",
    73: "\u4e2d\u96ea / Moderate snow",
    75: "\u5927\u96ea / Heavy snow",
    80: "\u9635\u96e8 / Rain showers",
    95: "\u96f7\u66b4 / Thunderstorm",
}


class WeatherProvider:
    """Fetch current weather via Open-Meteo (free, no key required)."""

    BASE_URL = "https://api.open-meteo.com/v1/forecast"
    GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
    TIMEOUT = 8

    @classmethod
    def fetch(cls, location: str) -> dict[str, Any] | None:
        if requests is None:
            logger.error("provider_weather_no_requests")
            return None

        canonical = cls._canonical_location(location)
        coords = cls._resolve_coords(canonical)
        if coords is None:
            logger.warning("provider_weather_no_coords location=%s canonical=%s", location, canonical)
            return None

        lat, lon = coords
        logger.info("provider_weather_fetching location=%s canonical=%s lat=%.4f lon=%.4f", location, canonical, lat, lon)

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
            logger.warning("provider_weather_api_error location=%s canonical=%s error=%s", location, canonical, exc)
            return None

        current = data.get("current", {})
        code = current.get("weather_code", 0)
        return {
            "temperature_c": current.get("temperature_2m"),
            "feels_like_c": current.get("apparent_temperature"),
            "humidity_pct": current.get("relative_humidity_2m"),
            "wind_kmh": current.get("wind_speed_10m"),
            "condition": _WMO_CODES.get(code, "Unknown"),
            "condition_code": code,
            "city": canonical,
        }

    @classmethod
    def _resolve_coords(cls, location: str) -> tuple[float, float] | None:
        canonical = cls._canonical_location(location)
        if canonical in _KNOWN_COORDS:
            return _KNOWN_COORDS[canonical]
        if canonical in _GEO_CACHE:
            return _GEO_CACHE[canonical]

        candidates = [canonical]
        stripped_city = re.sub(r"\bcity\b", "", canonical, flags=re.IGNORECASE).strip()
        if stripped_city and stripped_city not in candidates:
            candidates.append(stripped_city)
        if len(canonical) > 2 and canonical.endswith("\u5E02"):
            trimmed = canonical[:-1].strip()
            if trimmed and trimmed not in candidates:
                candidates.append(trimmed)

        for candidate in candidates:
            try:
                resp = requests.get(
                    cls.GEO_URL,
                    params={"name": candidate, "count": 1, "language": "en"},
                    timeout=cls.TIMEOUT,
                )
                resp.raise_for_status()
                results = resp.json().get("results", [])
                if results:
                    lat = float(results[0]["latitude"])
                    lon = float(results[0]["longitude"])
                    _GEO_CACHE[canonical] = (lat, lon)
                    return (lat, lon)
            except Exception as exc:
                logger.debug("provider_weather_geo_error location=%s candidate=%s error=%s", location, candidate, exc)
        return None

    @classmethod
    def _canonical_location(cls, location: str) -> str:
        text = str(location or "").strip()
        if not text:
            return ""
        text = re.split(r"[\uFF0C,\uFF08(]", text, maxsplit=1)[0].strip()
        if len(text) > 2 and text.endswith("\u5E02"):
            text = text[:-1].strip()
        text = re.sub(r"\bcity\b", "", text, flags=re.IGNORECASE).strip()
        alias = _LOCATION_ALIASES.get(text.lower())
        if alias:
            return alias
        return text
