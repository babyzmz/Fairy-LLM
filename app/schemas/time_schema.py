from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class TimeSchema:
    location: str = ""
    time_text: str = ""
    date_text: str = ""
    weekday: str = ""
    period: str = ""
    timezone: str = ""
    is_daytime: bool | None = None
    summary: str = ""

    @classmethod
    def repair(cls, payload: dict[str, Any]) -> "TimeSchema":
        def boolean(value: Any) -> bool | None:
            if value in {None, ""}:
                return None
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in {"1", "true", "yes", "on"}:
                return True
            if text in {"0", "false", "no", "off"}:
                return False
            return None

        return cls(
            location=str(payload.get("location", "") or "").strip(),
            time_text=str(payload.get("time_text", "") or payload.get("time") or "").strip(),
            date_text=str(payload.get("date_text", "") or payload.get("date") or "").strip(),
            weekday=str(payload.get("weekday", "") or "").strip(),
            period=str(payload.get("period", "") or "").strip(),
            timezone=str(payload.get("timezone", "") or "").strip(),
            is_daytime=boolean(payload.get("is_daytime")),
            summary=str(payload.get("summary", "") or "").strip(),
        )

    def is_valid(self) -> bool:
        return bool(self.location or self.time_text or self.date_text or self.timezone)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
