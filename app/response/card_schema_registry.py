from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from app.response.models import CardPayload


logger = logging.getLogger(__name__)

CardNormalizer = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class CardSchemaDefinition:
    card_type: str
    version: str
    normalizer: CardNormalizer


class CardSchemaRegistry:
    def __init__(self) -> None:
        self._registry: dict[str, CardSchemaDefinition] = {
            "weather": CardSchemaDefinition("weather", "1", self._normalize_weather),
            "location": CardSchemaDefinition("location", "1", self._normalize_location),
            "news_list": CardSchemaDefinition("news_list", "1", self._normalize_news_list),
            "generic_info": CardSchemaDefinition("generic_info", "1", self._normalize_generic_info),
        }
        self._legacy_type_map = {
            "weather_card": "weather",
            "location_map_card": "location",
            "map_card": "location",
            "news_card": "news_list",
            "time_card": "generic_info",
            "generic_info_card": "generic_info",
        }

    def normalize_card_payload(
        self,
        card_type: str,
        raw_data: dict[str, Any],
        *,
        layout: str = "single",
        fallback_reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> CardPayload | None:
        canonical_type = self._canonical_type(card_type)
        definition = self._registry.get(canonical_type)
        payload_metadata = dict(metadata or {})
        payload_metadata.setdefault("requested_type", str(card_type or "").strip().lower())
        if definition is None:
            logger.info(
                "card_schema_fallback raw_type=%s fallback=generic_info reason=%s",
                card_type,
                fallback_reason or "unknown_type",
            )
            canonical_type = "generic_info"
            definition = self._registry[canonical_type]
            raw_data = self._fallback_generic_data(card_type, raw_data)
            payload_metadata["fallback_reason"] = fallback_reason or "unknown_type"

        normalized = definition.normalizer(dict(raw_data or {}))
        if not normalized:
            logger.info(
                "card_schema_skipped type=%s reason=%s",
                definition.card_type,
                fallback_reason or "empty_after_normalization",
            )
            return None

        logger.info(
            "card_schema_normalized type=%s version=%s layout=%s keys=%s",
            definition.card_type,
            definition.version,
            layout,
            ",".join(sorted(normalized.keys())),
        )
        payload_metadata.setdefault("normalized_type", definition.card_type)
        payload_metadata.setdefault("schema_version", definition.version)
        if fallback_reason:
            payload_metadata.setdefault("fallback_reason", fallback_reason)
        return CardPayload(
            type=definition.card_type,
            version=definition.version,
            data=normalized,
            layout=layout,
            metadata=payload_metadata,
        )

    def knows(self, card_type: str) -> bool:
        return self._canonical_type(card_type) in self._registry

    def supported_types(self) -> list[str]:
        return sorted(self._registry.keys())

    def _canonical_type(self, card_type: str) -> str:
        normalized = str(card_type or "").strip().lower()
        return self._legacy_type_map.get(normalized, normalized or "generic_info")

    def _normalize_weather(self, raw: dict[str, Any]) -> dict[str, Any]:
        condition = self._string(raw.get("condition") or raw.get("weather_label"))
        city = self._string(raw.get("city") or raw.get("weather_location"))
        wind_kmh = self._number(raw.get("wind_kmh"))
        if wind_kmh is None:
            wind_kmh = self._extract_number(raw.get("wind"))
        normalized = {
            "city": city,
            "country": self._string(raw.get("country")),
            "condition": condition,
            "temperature_c": self._number(raw.get("temperature_c", raw.get("temp"))),
            "high_c": self._number(raw.get("high_c", raw.get("high"))),
            "low_c": self._number(raw.get("low_c", raw.get("low"))),
            "feels_like_c": self._number(raw.get("feels_like_c", raw.get("feels_like"))),
            "humidity_percent": self._number(raw.get("humidity_percent", raw.get("humidity"))),
            "wind_kmh": wind_kmh,
            "icon_code": self._string(raw.get("icon_code") or raw.get("icon_type")),
            "icon_key": self._string(raw.get("icon_key") or raw.get("icon_code") or raw.get("icon_type")),
            "icon_path": self._string(raw.get("icon_path")),
            "condition_key": self._string(raw.get("condition_key") or raw.get("condition")),
            "summary": self._string(raw.get("summary")),
            "hourly_curve": self._number_list(raw.get("hourly_curve")),
        }
        if not any(not self._is_empty_value(normalized.get(key)) for key in ("city", "condition", "temperature_c", "high_c", "low_c")):
            return {}
        return normalized

    def _normalize_location(self, raw: dict[str, Any]) -> dict[str, Any]:
        title = self._string(raw.get("title") or raw.get("place_name") or raw.get("address"))
        address = self._string(raw.get("address") or title)
        city, region, country = self._split_location_parts(address)
        lat = self._number(raw.get("lat"))
        lon = self._number(raw.get("lon"))
        normalized = {
            "title": title,
            "address": address,
            "city": self._string(raw.get("city")) or city,
            "region": self._string(raw.get("region")) or region,
            "country": self._string(raw.get("country")) or country,
            "lat": lat,
            "lon": lon,
            "distance_text": self._string(raw.get("distance_text") or raw.get("distance")),
            "map_preview_path": self._string(raw.get("map_preview_path") or raw.get("map_preview_url") or raw.get("image_path") or raw.get("image_url")),
            "map_preview_url": self._string(raw.get("map_preview_path") or raw.get("map_preview_url") or raw.get("image_path") or raw.get("image_url")),
            "external_map_url": self._string(raw.get("external_map_url") or raw.get("map_url") or raw.get("url")),
            "summary": self._string(raw.get("summary")),
        }
        if not any(normalized.get(key) not in {"", None} for key in ("title", "address", "external_map_url")) and (lat is None or lon is None):
            return {}
        return normalized

    def _normalize_news_list(self, raw: dict[str, Any]) -> dict[str, Any]:
        items = raw.get("items")
        if not isinstance(items, list):
            return {}
        normalized_items: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            headline = self._string(item.get("headline") or item.get("title"))
            url = self._string(item.get("url"))
            if not headline or not url:
                continue
            normalized_items.append(
                {
                    "headline": headline,
                    "source": self._string(item.get("source")) or self._extract_source_from_url(url),
                    "published_at": self._string(item.get("published_at")),
                    "summary": self._string(item.get("summary") or item.get("short_comment")),
                    "image_path": self._string(item.get("image_path") or item.get("image_url") or item.get("image") or item.get("thumbnail")),
                    "url": url,
                    "tags": [self._string(tag) for tag in list(item.get("tags", []) or []) if self._string(tag)],
                }
            )
        if not normalized_items:
            return {}
        return {
            "title": self._string(raw.get("title")) or "News Briefing",
            "items": normalized_items,
        }

    def _normalize_generic_info(self, raw: dict[str, Any]) -> dict[str, Any]:
        title = self._string(raw.get("title") or raw.get("summary") or raw.get("label")) or "Structured info"
        summary = self._string(raw.get("summary") or raw.get("text") or raw.get("body"))
        normalized_fields = self._normalize_generic_fields(raw.get("fields"))
        if not normalized_fields:
            normalized_fields = self._generic_fields_from_dict(raw)
        if not summary and normalized_fields:
            summary = "; ".join(f"{field['label']}: {field['value']}" for field in normalized_fields[:3])
        if not summary and not normalized_fields:
            return {}
        return {
            "title": title,
            "summary": summary,
            "fields": normalized_fields,
        }

    def _fallback_generic_data(self, raw_type: str, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": self._string(raw.get("title")) or raw_type or "Structured info",
            "summary": self._string(raw.get("summary") or raw.get("text") or raw.get("body")),
            "fields": self._generic_fields_from_dict(raw),
        }

    def _normalize_generic_fields(self, fields: Any) -> list[dict[str, str]]:
        if not isinstance(fields, list):
            return []
        normalized: list[dict[str, str]] = []
        for field in fields:
            if not isinstance(field, dict):
                continue
            label = self._string(field.get("label") or field.get("name"))
            value = self._string(field.get("value"))
            if not label or not value:
                continue
            normalized.append({"label": label, "value": value})
        return normalized

    def _generic_fields_from_dict(self, raw: dict[str, Any]) -> list[dict[str, str]]:
        ignored = {
            "title",
            "summary",
            "text",
            "body",
            "fields",
            "items",
            "url",
            "image_url",
            "layout_mode",
            "card_type",
            "card_version",
        }
        fields: list[dict[str, str]] = []
        for key, value in raw.items():
            if key in ignored or self._is_empty_value(value):
                continue
            if isinstance(value, (dict, list)):
                continue
            fields.append({"label": str(key).replace("_", " ").title(), "value": self._string(value)})
        return fields[:6]

    def _split_location_parts(self, address: str) -> tuple[str, str, str]:
        if not address:
            return "", "", ""
        parts = [part.strip() for part in str(address).split(",") if part.strip()]
        if len(parts) >= 3:
            return parts[0], parts[-2], parts[-1]
        if len(parts) == 2:
            return parts[0], "", parts[-1]
        return "", "", ""

    def _extract_source_from_url(self, url: str) -> str:
        cleaned = str(url or "").strip()
        if "//" in cleaned:
            cleaned = cleaned.split("//", 1)[1]
        return cleaned.split("/", 1)[0].strip()

    def _string(self, value: Any) -> str:
        return str(value or "").strip()

    def _number(self, value: Any) -> float | None:
        try:
            if value in {None, ""}:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _extract_number(self, value: Any) -> float | None:
        text = self._string(value)
        if not text:
            return None
        token = []
        for char in text:
            if char.isdigit() or char in ".-":
                token.append(char)
            elif token:
                break
        if not token:
            return None
        try:
            return float("".join(token))
        except ValueError:
            return None

    def _number_list(self, value: Any) -> list[float]:
        if not isinstance(value, list):
            return []
        items: list[float] = []
        for item in value:
            number = self._number(item)
            if number is not None:
                items.append(number)
        return items

    def _is_empty_value(self, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == ""
        if isinstance(value, (list, tuple, set, dict)):
            return len(value) == 0
        return False
