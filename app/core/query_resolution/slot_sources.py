from __future__ import annotations

import os


DEFAULT_CITY_ENV_KEYS = ("FAIRY_DEFAULT_CITY", "APP_DEFAULT_CITY")


def get_configured_default_city() -> str:
    for key in DEFAULT_CITY_ENV_KEYS:
        value = str(os.getenv(key) or "").strip()
        if value:
            return value
    return ""
