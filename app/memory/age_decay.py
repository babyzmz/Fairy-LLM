from __future__ import annotations

import math
from datetime import datetime, timezone


DEFAULT_HALF_LIFE_DAYS = 14.0


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def age_decay_weight(
    updated_at: str | datetime | None,
    *,
    now: datetime | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> float:
    if updated_at is None:
        return 0.5
    if isinstance(updated_at, str):
        parsed = parse_timestamp(updated_at)
    else:
        parsed = updated_at
    if parsed is None:
        return 0.5
    current = now or datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta_days = max(0.0, (current - parsed).total_seconds() / 86400.0)
    half_life = max(0.1, half_life_days)
    return math.pow(0.5, delta_days / half_life)


def freshness_score(updated_at: str | datetime | None, *, half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> float:
    return age_decay_weight(updated_at, half_life_days=half_life_days)
