from __future__ import annotations

from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fairy_core.information import (
    InformationCapabilityHealth,
    InformationCapabilityStatus,
    MapResult,
    TimeResult,
)


class TimeZoneError(ValueError):
    error_code = "TIMEZONE_NOT_FOUND"


class TimeZoneService:
    def __init__(self, *, clock=lambda: datetime.now(UTC)) -> None:
        self._clock = clock

    def current(self, timezone_name: str) -> TimeResult:
        normalized = timezone_name.strip()
        if not normalized or len(normalized) > 255 or "/" not in normalized:
            raise TimeZoneError("timezone must be an IANA database name")
        try:
            zone = ZoneInfo(normalized)
        except ZoneInfoNotFoundError as error:
            raise TimeZoneError("timezone was not found") from error
        observed_at = self._clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("timezone clock must be timezone-aware")
        local = observed_at.astimezone(zone)
        offset = local.utcoffset()
        daylight = local.dst()
        assert offset is not None and daylight is not None
        return TimeResult(
            provider="python_zoneinfo",
            observed_at=observed_at,
            freshness="installed_iana_tzdata",
            source_url="https://www.iana.org/time-zones",
            diagnostics=(f"tzdata={_tzdata_version()}",),
            timezone=normalized,
            local_time=local,
            utc_offset_seconds=int(offset.total_seconds()),
            is_dst=bool(daylight.total_seconds()),
        )

    def health(self) -> InformationCapabilityHealth:
        observed_at = self._clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("timezone clock must be timezone-aware")
        return InformationCapabilityHealth(
            provider="python_zoneinfo",
            status=InformationCapabilityStatus.AVAILABLE,
            observed_at=observed_at,
            error_code=None,
            diagnostics=(f"tzdata={_tzdata_version()}",),
        )


def openstreetmap_search(query: str, *, observed_at: datetime | None = None) -> MapResult:
    normalized = query.strip()
    if not normalized or len(normalized) > 1_000:
        raise ValueError("map query is invalid")
    at = observed_at or datetime.now(UTC)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("map observed_at must be timezone-aware")
    source_url = "https://www.openstreetmap.org/search?" + urlencode({"query": normalized})
    return MapResult(
        provider="openstreetmap",
        observed_at=at,
        freshness="link_generated",
        source_url=source_url,
        diagnostics=(),
        query=normalized,
    )


def _tzdata_version() -> str:
    try:
        return version("tzdata")
    except PackageNotFoundError:
        return "system"


__all__ = ["TimeZoneError", "TimeZoneService", "openstreetmap_search"]
