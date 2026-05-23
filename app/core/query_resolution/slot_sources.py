from __future__ import annotations

import os
import threading
import time

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]


DEFAULT_CITY_ENV_KEYS = ("FAIRY_DEFAULT_CITY", "APP_DEFAULT_CITY")
CURRENT_CITY_ENV_KEYS = ("FAIRY_CURRENT_CITY", "APP_CURRENT_CITY")
_CURRENT_CITY_CACHE: dict[str, object] = {"city": "", "expires_at": 0.0}
_CURRENT_CITY_LOCK = threading.Lock()


def get_configured_default_city() -> str:
    for key in DEFAULT_CITY_ENV_KEYS:
        value = str(os.getenv(key) or "").strip()
        if value:
            return value
    return ""


def get_detected_current_city(*, ttl_seconds: int = 1800) -> str:
    for key in CURRENT_CITY_ENV_KEYS:
        value = str(os.getenv(key) or "").strip()
        if value:
            return value

    now = time.time()
    cached_city = str(_CURRENT_CITY_CACHE.get("city") or "").strip()
    cached_expires_at = float(_CURRENT_CITY_CACHE.get("expires_at") or 0.0)
    if cached_city and cached_expires_at > now:
        return cached_city

    if requests is None:
        return ""

    with _CURRENT_CITY_LOCK:
        now = time.time()
        cached_city = str(_CURRENT_CITY_CACHE.get("city") or "").strip()
        cached_expires_at = float(_CURRENT_CITY_CACHE.get("expires_at") or 0.0)
        if cached_city and cached_expires_at > now:
            return cached_city
        try:
            response = requests.get("https://ipwho.is/", timeout=6)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return ""
        if not isinstance(payload, dict) or payload.get("success") is False:
            return ""
        city = str(payload.get("city", "") or "").strip()
        region = str(payload.get("region", "") or "").strip()
        country = str(payload.get("country", "") or "").strip()
        location = city or region or country
        if not location:
            return ""
        _CURRENT_CITY_CACHE["city"] = location
        _CURRENT_CITY_CACHE["expires_at"] = now + max(300, int(ttl_seconds))
        return location
