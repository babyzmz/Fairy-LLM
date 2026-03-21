"""Time provider using Python's zoneinfo — Stage-1 structured provider.

Never derives time via offset math; always uses ZoneInfo(iana_name).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # Python < 3.9
    from backports.zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # type: ignore[no-redef]

# Ensure tzdata is importable on Windows (Python 3.10 has no bundled tz database)
try:
    import tzdata  # noqa: F401
except ImportError:
    pass

from app.agents.realtime_lookup.timezone_resolver import TimezoneResolver

logger = logging.getLogger(__name__)


class TimeProvider:
    """Return the current local time for a city using IANA timezone database."""

    @classmethod
    def fetch(cls, location: str) -> dict[str, Any] | None:
        """Return current time data for *location*.

        Returns:
            dict with keys: iso_datetime, date, time, weekday,
                            hour, period_zh, is_daytime, tz_name
            or None if timezone cannot be resolved.
        """
        tz_name = TimezoneResolver.resolve(location)
        if not tz_name:
            logger.warning("provider_time_no_tz location=%s", location)
            return None

        try:
            tz  = ZoneInfo(tz_name)
            now = datetime.now(tz)
        except (ZoneInfoNotFoundError, Exception) as exc:
            logger.warning("provider_time_zoneinfo_error tz=%s error=%s", tz_name, exc)
            return None

        hour = now.hour
        if 5 <= hour < 12:
            period = "早上"
        elif 12 <= hour < 14:
            period = "中午"
        elif 14 <= hour < 18:
            period = "下午"
        elif 18 <= hour < 22:
            period = "晚上"
        else:
            period = "深夜"

        logger.info(
            "provider_time_success location=%s tz=%s time=%s",
            location, tz_name, now.strftime("%H:%M"),
        )
        return {
            "iso_datetime": now.isoformat(),
            "date":         now.strftime("%Y-%m-%d"),
            "time":         now.strftime("%H:%M"),
            "weekday":      now.strftime("%A"),
            "hour":         hour,
            "period_zh":    period,
            "is_daytime":   5 <= hour < 20,
            "tz_name":      tz_name,
        }
