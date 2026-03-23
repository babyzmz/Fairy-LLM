from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class WeatherSchema:
    city: str = ""
    country: str = ""
    condition: str = ""
    temperature_c: float | None = None
    high_c: float | None = None
    low_c: float | None = None
    feels_like_c: float | None = None
    humidity_percent: float | None = None
    wind_kmh: float | None = None
    icon_code: str = ""
    icon_key: str = ""
    condition_key: str = ""
    summary: str = ""
    hourly_curve: list[float] = field(default_factory=list)

    @classmethod
    def repair(cls, payload: dict[str, Any]) -> "WeatherSchema":
        def number(value: Any) -> float | None:
            try:
                if value in {None, ""}:
                    return None
                return float(value)
            except (TypeError, ValueError):
                return None

        curve: list[float] = []
        for item in list(payload.get("hourly_curve", []) or []):
            parsed = number(item)
            if parsed is not None:
                curve.append(parsed)
        return cls(
            city=str(payload.get("city", "") or "").strip(),
            country=str(payload.get("country", "") or "").strip(),
            condition=str(payload.get("condition", "") or "").strip(),
            temperature_c=number(payload.get("temperature_c")),
            high_c=number(payload.get("high_c")),
            low_c=number(payload.get("low_c")),
            feels_like_c=number(payload.get("feels_like_c")),
            humidity_percent=number(payload.get("humidity_percent")),
            wind_kmh=number(payload.get("wind_kmh")),
            icon_code=str(payload.get("icon_code", "") or "").strip(),
            icon_key=str(payload.get("icon_key", "") or "").strip(),
            condition_key=str(payload.get("condition_key", "") or "").strip(),
            summary=str(payload.get("summary", "") or "").strip(),
            hourly_curve=curve,
        )

    def is_valid(self) -> bool:
        return bool(self.city or self.condition or self.temperature_c is not None or self.high_c is not None or self.low_c is not None)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
