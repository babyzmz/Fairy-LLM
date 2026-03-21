"""Global timezone resolver — Stage 3 critical fix.

Resolution order:
  1. Static IANA map (instant, covers ~100 cities)
  2. Search fallback → parse IANA name from snippet
  3. Cache resolved mappings to avoid repeat searches

Time MUST be computed as datetime.now(ZoneInfo(iana_name)).
Never derive by offset arithmetic.
"""

from __future__ import annotations

import logging
import re
from typing import Callable

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Static IANA map  (city / Chinese name → IANA timezone string)
# -------------------------------------------------------------------
_STATIC_MAP: dict[str, str] = {
    # Australia
    "Melbourne":     "Australia/Melbourne",
    "墨尔本":          "Australia/Melbourne",
    "Sydney":        "Australia/Sydney",
    "悉尼":           "Australia/Sydney",
    "Brisbane":      "Australia/Brisbane",
    "布里斯班":         "Australia/Brisbane",
    "Perth":         "Australia/Perth",
    "珀斯":           "Australia/Perth",
    "Adelaide":      "Australia/Adelaide",
    "阿德莱德":         "Australia/Adelaide",
    "Canberra":      "Australia/Sydney",
    "堪培拉":          "Australia/Sydney",
    "Hobart":        "Australia/Hobart",
    "Darwin":        "Australia/Darwin",
    "Forest Hill":   "Australia/Melbourne",
    # Asia
    "Tokyo":         "Asia/Tokyo",
    "东京":           "Asia/Tokyo",
    "Osaka":         "Asia/Tokyo",
    "大阪":           "Asia/Tokyo",
    "Beijing":       "Asia/Shanghai",
    "北京":           "Asia/Shanghai",
    "Shanghai":      "Asia/Shanghai",
    "上海":           "Asia/Shanghai",
    "Shenzhen":      "Asia/Shanghai",
    "深圳":           "Asia/Shanghai",
    "Guangzhou":     "Asia/Shanghai",
    "广州":           "Asia/Shanghai",
    "Chengdu":       "Asia/Shanghai",
    "成都":           "Asia/Shanghai",
    "Hangzhou":      "Asia/Shanghai",
    "杭州":           "Asia/Shanghai",
    "Wuhan":         "Asia/Shanghai",
    "武汉":           "Asia/Shanghai",
    "Hong Kong":     "Asia/Hong_Kong",
    "香港":           "Asia/Hong_Kong",
    "Taipei":        "Asia/Taipei",
    "台北":           "Asia/Taipei",
    "Singapore":     "Asia/Singapore",
    "新加坡":          "Asia/Singapore",
    "Bangkok":       "Asia/Bangkok",
    "曼谷":           "Asia/Bangkok",
    "Jakarta":       "Asia/Jakarta",
    "雅加达":          "Asia/Jakarta",
    "Kuala Lumpur":  "Asia/Kuala_Lumpur",
    "吉隆坡":          "Asia/Kuala_Lumpur",
    "Seoul":         "Asia/Seoul",
    "首尔":           "Asia/Seoul",
    "Dubai":         "Asia/Dubai",
    "迪拜":           "Asia/Dubai",
    "Mumbai":        "Asia/Kolkata",
    "孟买":           "Asia/Kolkata",
    "Kolkata":       "Asia/Kolkata",
    "New Delhi":     "Asia/Kolkata",
    "Delhi":         "Asia/Kolkata",
    "新德里":          "Asia/Kolkata",
    "Karachi":       "Asia/Karachi",
    "卡拉奇":          "Asia/Karachi",
    "Dhaka":         "Asia/Dhaka",
    "达卡":           "Asia/Dhaka",
    "Colombo":       "Asia/Colombo",
    "科伦坡":          "Asia/Colombo",
    "Tashkent":      "Asia/Tashkent",
    "Riyadh":        "Asia/Riyadh",
    "利雅得":          "Asia/Riyadh",
    # Europe
    "London":        "Europe/London",
    "伦敦":           "Europe/London",
    "Paris":         "Europe/Paris",
    "巴黎":           "Europe/Paris",
    "Berlin":        "Europe/Berlin",
    "柏林":           "Europe/Berlin",
    "Moscow":        "Europe/Moscow",
    "莫斯科":          "Europe/Moscow",
    "Amsterdam":     "Europe/Amsterdam",
    "阿姆斯特丹":        "Europe/Amsterdam",
    "Madrid":        "Europe/Madrid",
    "马德里":          "Europe/Madrid",
    "Rome":          "Europe/Rome",
    "罗马":           "Europe/Rome",
    "Vienna":        "Europe/Vienna",
    "维也纳":          "Europe/Vienna",
    "Warsaw":        "Europe/Warsaw",
    "华沙":           "Europe/Warsaw",
    "Stockholm":     "Europe/Stockholm",
    "斯德哥尔摩":        "Europe/Stockholm",
    "Zurich":        "Europe/Zurich",
    "苏黎世":          "Europe/Zurich",
    "Istanbul":      "Europe/Istanbul",
    "伊斯坦布尔":        "Europe/Istanbul",
    "Athens":        "Europe/Athens",
    "雅典":           "Europe/Athens",
    "Lisbon":        "Europe/Lisbon",
    "里斯本":          "Europe/Lisbon",
    "Dublin":        "Europe/Dublin",
    "都柏林":          "Europe/Dublin",
    "Prague":        "Europe/Prague",
    "布拉格":          "Europe/Prague",
    "Helsinki":      "Europe/Helsinki",
    "赫尔辛基":         "Europe/Helsinki",
    "Copenhagen":    "Europe/Copenhagen",
    "哥本哈根":         "Europe/Copenhagen",
    "Brussels":      "Europe/Brussels",
    "布鲁塞尔":         "Europe/Brussels",
    "Bucharest":     "Europe/Bucharest",
    "布加勒斯特":        "Europe/Bucharest",
    # Americas
    "New York":      "America/New_York",
    "纽约":           "America/New_York",
    "Los Angeles":   "America/Los_Angeles",
    "洛杉矶":          "America/Los_Angeles",
    "Chicago":       "America/Chicago",
    "芝加哥":          "America/Chicago",
    "Houston":       "America/Chicago",
    "休斯顿":          "America/Chicago",
    "Toronto":       "America/Toronto",
    "多伦多":          "America/Toronto",
    "Vancouver":     "America/Vancouver",
    "温哥华":          "America/Vancouver",
    "Mexico City":   "America/Mexico_City",
    "墨西哥城":         "America/Mexico_City",
    "Sao Paulo":     "America/Sao_Paulo",
    "圣保罗":          "America/Sao_Paulo",
    "Buenos Aires":  "America/Argentina/Buenos_Aires",
    "布宜诺斯艾利斯":      "America/Argentina/Buenos_Aires",
    "Santiago":      "America/Santiago",
    "圣地亚哥":         "America/Santiago",
    "Bogota":        "America/Bogota",
    "波哥大":          "America/Bogota",
    "Lima":           "America/Lima",
    "利马":           "America/Lima",
    # Africa
    "Cairo":         "Africa/Cairo",
    "开罗":           "Africa/Cairo",
    "Lagos":         "Africa/Lagos",
    "拉各斯":          "Africa/Lagos",
    "Nairobi":       "Africa/Nairobi",
    "内罗毕":          "Africa/Nairobi",
    "Johannesburg":  "Africa/Johannesburg",
    "约翰内斯堡":        "Africa/Johannesburg",
    "Cape Town":     "Africa/Johannesburg",
    "开普敦":          "Africa/Johannesburg",
    "Casablanca":    "Africa/Casablanca",
    "卡萨布兰卡":        "Africa/Casablanca",
    # Pacific
    "Auckland":      "Pacific/Auckland",
    "奥克兰":          "Pacific/Auckland",
    "Honolulu":      "Pacific/Honolulu",
    "火奴鲁鲁":         "Pacific/Honolulu",
    "Fiji":          "Pacific/Fiji",
    "斐济":           "Pacific/Fiji",
}

# Runtime search cache
_SEARCH_CACHE: dict[str, str] = {}

# Patterns to extract IANA tz from search snippets
_IANA_PATTERNS = [
    re.compile(r'\b((?:Africa|America|Antarctica|Asia|Atlantic|Australia|Europe|Indian|Pacific)/[\w/]+)\b'),
    re.compile(r'IANA[^:]*:\s*([A-Z][a-z]+/[\w/]+)'),
]


class TimezoneResolver:
    """Resolve city name → IANA timezone string.

    Never returns a raw UTC offset string.  Always returns a full IANA name
    suitable for ZoneInfo(iana_name).
    """

    @classmethod
    def resolve(
        cls,
        location: str,
        search_fn: Callable[[str], list[dict]] | None = None,
    ) -> str | None:
        """Return IANA timezone for *location*, or None if unresolvable.

        Args:
            location:  City or place name (Chinese or English).
            search_fn: Optional search_web callable for dynamic fallback.
        """
        # 1. Static map (instant)
        tz = _STATIC_MAP.get(location)
        if tz:
            logger.debug("tz_static_hit location=%s tz=%s", location, tz)
            return tz

        # 2. Case-insensitive static map lookup
        location_lower = location.lower()
        for key, val in _STATIC_MAP.items():
            if key.lower() == location_lower:
                logger.debug("tz_static_ci_hit location=%s tz=%s", location, val)
                return val

        # 3. Search cache
        if location in _SEARCH_CACHE:
            logger.debug("tz_cache_hit location=%s tz=%s", location, _SEARCH_CACHE[location])
            return _SEARCH_CACHE[location]

        # 4. Dynamic search fallback
        if search_fn is not None:
            tz = cls._resolve_via_search(location, search_fn)
            if tz:
                _SEARCH_CACHE[location] = tz
                logger.info("tz_search_resolved location=%s tz=%s", location, tz)
                return tz

        logger.warning("tz_unresolvable location=%s", location)
        return None

    @classmethod
    def _resolve_via_search(cls, location: str, search_fn: Callable) -> str | None:
        """Search for IANA timezone name dynamically."""
        queries = [
            f"{location} timezone IANA name",
            f"{location} time zone",
        ]
        for query in queries:
            try:
                results = search_fn(query, max_results=3)
                for result in results:
                    snippet = result.get("snippet", "") + " " + result.get("title", "")
                    tz = cls._extract_iana_from_text(snippet)
                    if tz:
                        return tz
            except Exception as exc:
                logger.debug("tz_search_error query=%s error=%s", query, exc)
        return None

    @staticmethod
    def _extract_iana_from_text(text: str) -> str | None:
        """Extract IANA timezone name from free text."""
        for pattern in _IANA_PATTERNS:
            m = pattern.search(text)
            if m:
                candidate = m.group(1)
                # Validate it looks real
                if "/" in candidate and len(candidate) > 5:
                    return candidate
        return None

    @classmethod
    def bulk_resolve(cls, locations: list[str]) -> dict[str, str | None]:
        """Resolve multiple locations at once (no search fallback)."""
        return {loc: cls.resolve(loc) for loc in locations}
