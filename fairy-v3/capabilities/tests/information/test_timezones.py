from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest

from fairy_capabilities.information.timezones import (
    TimeZoneError,
    TimeZoneService,
    openstreetmap_search,
)


@pytest.mark.parametrize(
    ("instant", "offset_seconds", "is_dst"),
    [
        (datetime(2026, 1, 15, tzinfo=UTC), 11 * 3600, True),
        (datetime(2026, 7, 15, tzinfo=UTC), 10 * 3600, False),
    ],
)
def test_timezone_conversion_tracks_dst(
    instant: datetime,
    offset_seconds: int,
    is_dst: bool,
) -> None:
    result = TimeZoneService(clock=lambda: instant).current("Australia/Sydney")

    assert result.timezone == "Australia/Sydney"
    assert result.utc_offset_seconds == offset_seconds
    assert result.is_dst is is_dst
    assert result.local_time.utcoffset().total_seconds() == offset_seconds
    assert result.provider == "python_zoneinfo"


def test_timezone_rejects_unknown_or_non_iana_names() -> None:
    service = TimeZoneService()

    with pytest.raises(TimeZoneError) as captured:
        service.current("Sydney local")

    assert captured.value.error_code == "TIMEZONE_NOT_FOUND"


def test_openstreetmap_deep_link_preserves_unicode_and_reserved_text() -> None:
    result = openstreetmap_search(
        "Café & Museum / Sydney",
        observed_at=datetime(2026, 7, 11, tzinfo=UTC),
    )
    parsed = urlsplit(result.source_url)

    assert parsed.scheme == "https"
    assert parsed.netloc == "www.openstreetmap.org"
    assert parsed.path == "/search"
    assert parse_qs(parsed.query) == {"query": ["Café & Museum / Sydney"]}
    assert "%26" in result.source_url
    assert result.freshness == "link_generated"
