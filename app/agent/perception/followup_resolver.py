from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class FollowUpContext:
    target: str = ""
    reused: bool = False
    reason: str = ""
    focus_value: str = ""


class FollowUpResolver:
    _MAP_DISPLAY_PATTERNS = (
        "显示地图",
        "给我看看地图",
        "把地图打开看看",
        "地图展示一下",
        "地图显示出来",
        "看看地图",
        "show map",
        "show me the map",
        "display map",
        "display the map",
        "open the map",
    )
    _WEATHER_FOLLOWUP_PATTERNS = (
        "那天气呢",
        "天气呢",
        "weather there",
        "how about the weather",
    )
    _LOCATION_DEICTIC_PATTERNS = (
        "这个地方在哪",
        "这个地方在哪里",
        "再给我看看",
        "再看看",
        "that place",
        "this place",
    )
    _SHORT_ENTITY_PATTERN = re.compile(
        r"^(?:那|那么|那边|那儿|那里)?(?P<subject>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z路街区县市州省国\-\s]{1,24}?)(?:呢|怎么样|如何)?\??$",
        flags=re.IGNORECASE,
    )

    def resolve(self, normalized_text: str, *, previous_structured: dict[str, Any] | None = None) -> FollowUpContext:
        text = str(normalized_text or "").strip()
        lowered = text.lower()
        payload = previous_structured if isinstance(previous_structured, dict) else {}
        nested_location = payload.get("location") if isinstance(payload.get("location"), dict) else {}
        source = nested_location or payload
        structured_type = self._structured_type(payload)
        location_value = self._location_value(source)
        weather_value = self._weather_focus_value(source) or location_value

        if not payload:
            return FollowUpContext()

        if self._contains_any(lowered, self._MAP_DISPLAY_PATTERNS) and location_value:
            return FollowUpContext(target="location", reused=True, reason="map_display_followup", focus_value=location_value)

        if self._contains_any(lowered, self._WEATHER_FOLLOWUP_PATTERNS):
            if structured_type in {"weather", "weather_lookup", "location", "location_lookup"}:
                return FollowUpContext(target="weather", reused=True, reason="weather_followup", focus_value=weather_value)

        subject = self._extract_short_subject(text)
        if subject and structured_type in {"weather", "weather_lookup"}:
            return FollowUpContext(target="weather", reused=True, reason="elliptical_weather_followup", focus_value=subject)

        if subject and structured_type in {"location", "location_lookup"}:
            return FollowUpContext(target="location", reused=True, reason="elliptical_location_followup", focus_value=subject)

        if self._contains_any(lowered, self._LOCATION_DEICTIC_PATTERNS) and location_value:
            return FollowUpContext(target="location", reused=True, reason="location_deictic_followup", focus_value=location_value)

        return FollowUpContext()

    def resolve_target(self, normalized_text: str, *, previous_structured: dict[str, Any] | None = None) -> str:
        return self.resolve(normalized_text, previous_structured=previous_structured).target

    def _structured_type(self, payload: dict[str, Any]) -> str:
        routing = payload.get("routing") if isinstance(payload.get("routing"), dict) else {}
        intent_payload = payload.get("intent") if isinstance(payload.get("intent"), dict) else {}
        explicit = str(
            payload.get("type")
            or payload.get("card_type")
            or payload.get("card_schema_type")
            or routing.get("primary_intent")
            or intent_payload.get("intent_type")
            or ""
        ).strip().lower()
        if explicit:
            if explicit == "weather":
                return "weather_lookup"
            if explicit in {"location", "map_preview"}:
                return "location_lookup"
            return explicit
        if any(str(payload.get(key) or "").strip() for key in ("temp", "temperature_c", "weather_location", "weather_label", "condition")):
            return "weather_lookup"
        if any(str(payload.get(key) or "").strip() for key in ("lat", "lon", "map_url", "address", "title", "place_name")):
            return "location_lookup"
        return ""

    def _location_value(self, payload: dict[str, Any]) -> str:
        return str(
            payload.get("title")
            or payload.get("address")
            or payload.get("place_name")
            or payload.get("city")
            or payload.get("weather_location")
            or ""
        ).strip()

    def _weather_focus_value(self, payload: dict[str, Any]) -> str:
        return str(payload.get("city") or payload.get("weather_location") or payload.get("title") or "").strip()

    def _extract_short_subject(self, text: str) -> str:
        if not text or len(text) > 30:
            return ""
        match = self._SHORT_ENTITY_PATTERN.match(text)
        if not match:
            return ""
        subject = str(match.group("subject") or "").strip()
        if not subject:
            return ""
        blocked = {"天气", "地图", "这里", "那里", "这个地方", "那个地方"}
        if subject in blocked:
            return ""
        return subject

    def _contains_any(self, lowered: str, patterns: tuple[str, ...]) -> bool:
        return any(pattern in lowered for pattern in patterns)
