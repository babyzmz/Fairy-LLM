from __future__ import annotations

import re
from typing import Any

from .rule_models import RuleArbitrationContext


_WEATHER_TERMS = ("天气", "气温", "温度", "forecast", "weather", "下雨", "冷不冷")
_TIME_TERMS = ("几点", "时间", "当地时间", "current time", "what time", "time now", "现在几点")
_LOCATION_TERMS = ("在哪里", "在哪", "where is", "地址", "位置", "地点", "导航到")
_MAP_TERMS = ("地图", "show map", "display map", "open map", "看看地图", "打开地图", "显示地图")
_SHORT_FOLLOWUP_MARKERS = ("呢", "那", "现在呢", "那现在呢", "怎么样", "那边")

_SHORT_LOCATION_PATTERN = re.compile(
    r"^(?:那|那么|那边|那儿|那里)?(?P<location>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z\-\s]{1,24}?)(?:呢|怎么样|如何|现在呢)?\??$",
    flags=re.IGNORECASE,
)

_DIRECT_LOCATION_PATTERNS = (
    re.compile(r"^(?:显示|打开|看看|看一看|查看)?\s*(?P<location>.+?)\s*地图$", re.IGNORECASE),
    re.compile(r"^(?P<location>.+?)\s*(?:在哪里|在哪儿|在哪)$", re.IGNORECASE),
    re.compile(r"^(?:导航到|导航去|前往)\s*(?P<location>.+)$", re.IGNORECASE),
    re.compile(r"^(?:看看|看一看|查看)\s*(?P<location>.+?)\s*位置$", re.IGNORECASE),
    re.compile(r"^(?:show|display|open)\s+map\s+for\s+(?P<location>.+)$", re.IGNORECASE),
    re.compile(r"^(?:where\s+is)\s+(?P<location>.+)$", re.IGNORECASE),
)


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _first_location_entity(entities: list[Any]) -> str:
    for entity in entities:
        if getattr(entity, "kind", "") == "location":
            value = str(getattr(entity, "value", "") or "").strip()
            if value:
                return value
    return ""


def _extract_direct_location_target(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    for pattern in _DIRECT_LOCATION_PATTERNS:
        match = pattern.match(cleaned)
        if not match:
            continue
        candidate = str(match.group("location") or "").strip()
        if candidate:
            return candidate
    return ""


def apply_intent_rules(
    *,
    normalized_text: str,
    entities: list[Any],
    followup_target: str,
    session_context: Any,
) -> RuleArbitrationContext:
    lowered = str(normalized_text or "").strip().lower()
    context = RuleArbitrationContext()

    has_weather = _contains_any(lowered, _WEATHER_TERMS)
    has_time = _contains_any(lowered, _TIME_TERMS)
    has_location = _contains_any(lowered, _LOCATION_TERMS)
    has_map = _contains_any(lowered, _MAP_TERMS)
    has_followup_marker = _contains_any(lowered, _SHORT_FOLLOWUP_MARKERS)
    location_entity = _first_location_entity(entities)
    last_capability = str(getattr(session_context, "last_capability", "") or "").strip()
    direct_location = _extract_direct_location_target(normalized_text)

    short_match = _SHORT_LOCATION_PATTERN.match(str(normalized_text or "").strip())
    short_location = str(short_match.group("location") or "").strip() if short_match else ""
    if short_location and short_location not in {"天气", "地图", "时间"} and not location_entity:
        location_entity = short_location
    if direct_location and not location_entity:
        location_entity = direct_location

    if has_weather:
        context.matched_rules.append("realtime.weather")
        context.capability_boosts["weather_lookup"] = max(context.capability_boosts.get("weather_lookup", 0.0), 0.35)
        if not has_time:
            context.forced_capability = "weather_lookup"

    if has_time:
        context.matched_rules.append("realtime.time")
        context.capability_boosts["time_lookup"] = max(context.capability_boosts.get("time_lookup", 0.0), 0.40)
        context.forced_capability = "time_lookup"
        context.allow_sticky_bonus = False
        context.sticky_action = "switch"
        context.unstick_reasons.append("time_phrase_detected")

    if direct_location and (has_map or has_location):
        context.matched_rules.append("realtime.map_direct_location")
        context.capability_boosts["location_lookup"] = max(context.capability_boosts.get("location_lookup", 0.0), 0.48)
        context.slot_overrides["location"] = direct_location
        context.strict_override_slots.add("location")
        context.forced_capability = "location_lookup"
        context.allow_sticky_bonus = False
        context.sticky_action = "switch"
        context.unstick_reasons.append("map_direct_location_detected")
    elif has_map:
        context.matched_rules.append("realtime.map")
        context.capability_boosts["display_information"] = max(context.capability_boosts.get("display_information", 0.0), 0.40)
        if not has_time and not has_weather:
            context.forced_capability = "display_information"

    if has_location and not has_time and not has_weather and not has_map:
        context.matched_rules.append("realtime.location")
        context.capability_boosts["location_lookup"] = max(context.capability_boosts.get("location_lookup", 0.0), 0.34)
        context.forced_capability = context.forced_capability or "location_lookup"

    if followup_target and has_followup_marker:
        context.matched_rules.append("followup.marker")

    if location_entity and has_followup_marker and not (has_time or has_weather or has_map or has_location):
        context.matched_rules.append("followup.location_override")
        context.inferred_followup_type = "elliptical_location_override"
        context.slot_overrides["location"] = location_entity
        context.strict_override_slots.add("location")
        context.allow_sticky_bonus = False
        if followup_target == "weather" or last_capability == "weather_lookup":
            context.forced_capability = "weather_lookup"
        elif followup_target == "location" or last_capability in {"location_lookup", "display_information"}:
            context.forced_capability = "location_lookup"
        elif last_capability == "time_lookup":
            context.forced_capability = "time_lookup"
        context.sticky_action = "switch"
        context.unstick_reasons.append("location_override_followup")

    if location_entity and (has_time or has_weather or has_map or has_location):
        context.matched_rules.append("slot.location.explicit")
        context.slot_overrides["location"] = location_entity
        context.strict_override_slots.add("location")
        if last_capability and (
            (has_time and last_capability != "time_lookup")
            or (has_weather and last_capability != "weather_lookup")
            or (has_map and last_capability not in {"display_information", "location_lookup"})
        ):
            context.allow_sticky_bonus = False
            context.sticky_action = "switch"
            context.unstick_reasons.append("cross_capability_signal")

    if not context.inferred_followup_type and followup_target:
        context.inferred_followup_type = f"followup_{followup_target}"

    if not context.allow_sticky_bonus and context.sticky_action == "keep":
        context.sticky_action = "unstick"
    return context
