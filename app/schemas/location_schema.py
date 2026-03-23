from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class LocationSchema:
    title: str = ""
    address: str = ""
    city: str = ""
    region: str = ""
    country: str = ""
    lat: float | None = None
    lon: float | None = None
    distance_text: str = ""
    map_preview_path: str = ""
    map_preview_url: str = ""
    external_map_url: str = ""
    navigate_url: str = ""
    summary: str = ""

    @classmethod
    def repair(cls, payload: dict[str, Any]) -> "LocationSchema":
        def number(value: Any) -> float | None:
            try:
                if value in {None, ""}:
                    return None
                return float(value)
            except (TypeError, ValueError):
                return None

        return cls(
            title=str(payload.get("title", "") or "").strip(),
            address=str(payload.get("address", "") or "").strip(),
            city=str(payload.get("city", "") or "").strip(),
            region=str(payload.get("region", "") or "").strip(),
            country=str(payload.get("country", "") or "").strip(),
            lat=number(payload.get("lat")),
            lon=number(payload.get("lon")),
            distance_text=str(payload.get("distance_text", "") or "").strip(),
            map_preview_path=str(payload.get("map_preview_path") or payload.get("map_preview_url") or "").strip(),
            map_preview_url=str(payload.get("map_preview_url") or payload.get("map_preview_path") or "").strip(),
            external_map_url=str(payload.get("external_map_url", "") or "").strip(),
            navigate_url=str(payload.get("navigate_url") or payload.get("external_map_url") or "").strip(),
            summary=str(payload.get("summary", "") or "").strip(),
        )

    def is_valid(self) -> bool:
        return bool(self.title or self.address or ((self.lat is not None) and (self.lon is not None)) or self.external_map_url)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
