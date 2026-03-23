from __future__ import annotations

import logging
import re
from typing import Any

from app.response.models import ResponseRequestPlan


logger = logging.getLogger(__name__)


class ResponseModalityPlanner:
    _WEATHER_PATTERNS = (
        "weather",
        "forecast",
        "\u5929\u6c14",
        "\u6c14\u6e29",
        "\u6e29\u5ea6",
        "\u9884\u62a5",
        "\u4e0b\u96e8",
        "\u964d\u96e8",
    )
    _LOCATION_PATTERNS = (
        "where is",
        "where's",
        "nearby",
        "nearest",
        "post office",
        "\u5728\u54ea",
        "\u5728\u54ea\u91cc",
        "\u5730\u5740",
        "\u4f4d\u7f6e",
        "\u5730\u70b9",
        "\u5730\u56fe",
        "\u9644\u8fd1",
        "\u6700\u8fd1",
        "\u5750\u6807",
        "\u90ae\u5c40",
    )
    _MAP_DISPLAY_PATTERNS = (
        "show map",
        "show me the map",
        "display the map",
        "open the map",
        "\u663e\u793a\u5730\u56fe",
        "\u7ed9\u6211\u770b\u770b\u5730\u56fe",
        "\u628a\u5730\u56fe\u6253\u5f00\u770b\u770b",
        "\u5730\u56fe\u5c55\u793a\u4e00\u4e0b",
        "\u5730\u56fe\u663e\u793a\u4e00\u4e0b",
        "\u5730\u56fe\u663e\u793a\u51fa\u6765",
        "\u5730\u56fe\u6253\u5f00\u770b\u770b",
        "\u770b\u770b\u5730\u56fe",
    )
    _DESKTOP_CONTROL_VERBS = (
        "click",
        "close",
        "switch",
        "scroll",
        "type",
        "launch",
        "control",
        "\u70b9\u51fb",
        "\u70b9\u5f00",
        "\u5173\u95ed",
        "\u5207\u6362",
        "\u6eda\u52a8",
        "\u8f93\u5165",
        "\u62d6\u52a8",
        "\u9009\u4e2d",
        "\u6700\u5c0f\u5316",
        "\u6700\u5927\u5316",
        "\u63a7\u5236",
    )
    _SCREEN_REFERENTS = (
        "screen",
        "window",
        "desktop",
        "button",
        "menu",
        "app",
        "\u5c4f\u5e55",
        "\u754c\u9762",
        "\u7a97\u53e3",
        "\u6309\u94ae",
        "\u83dc\u5355",
        "\u8f6f\u4ef6",
        "\u5e94\u7528",
        "\u684c\u9762",
    )
    _NEWS_PATTERNS = (
        "tech news",
        "today's news",
        "latest news",
        "today news",
        "news",
        "\u65b0\u95fb",
        "\u79d1\u6280\u65b0\u95fb",
        "\u6700\u65b0",
        "\u5b9e\u65f6",
        "\u4eca\u65e5",
        "\u4eca\u5929",
        "\u5feb\u8baf",
        "\u52a8\u6001",
    )

    def plan_request(
        self,
        user_text: str,
        *,
        previous_structured: dict[str, Any] | None = None,
    ) -> ResponseRequestPlan:
        lowered = (user_text or "").strip().lower()
        context_payload = self._extract_location_context(previous_structured)

        if self._looks_like_weather(lowered):
            plan = ResponseRequestPlan(
                intent="weather",
                modality="text_plus_card",
                speech_mode="concise_structured",
                allow_voice_streaming=False,
                planner_confidence=0.96,
            )
        elif self._looks_like_location_followup(lowered, previous_structured):
            plan = ResponseRequestPlan(
                intent="location",
                modality="card_primary_text_summary",
                speech_mode="concise_structured",
                allow_voice_streaming=False,
                planner_confidence=0.98,
                force_card_type="location",
                context_payload=context_payload,
            )
        elif self._looks_like_location(lowered):
            plan = ResponseRequestPlan(
                intent="location",
                modality="card_primary_text_summary",
                speech_mode="concise_structured",
                allow_voice_streaming=False,
                planner_confidence=0.93,
                force_card_type="location",
                context_payload=context_payload,
            )
        elif self._looks_like_news(lowered):
            plan = ResponseRequestPlan(
                intent="news",
                modality="text_plus_card",
                speech_mode="summary_first",
                allow_voice_streaming=False,
                planner_confidence=0.92,
            )
        else:
            plan = ResponseRequestPlan(
                intent="text",
                modality="text_only",
                speech_mode="detailed_explainer",
                allow_voice_streaming=True,
                planner_confidence=0.68,
            )

        logger.info(
            "response_plan intent=%s planner_confidence=%.2f modality=%s speech_mode=%s force_card=%s",
            plan.intent,
            plan.planner_confidence,
            plan.modality,
            plan.speech_mode,
            plan.force_card_type or "",
        )
        return plan

    def _looks_like_weather(self, lowered: str) -> bool:
        return any(token in lowered for token in self._WEATHER_PATTERNS)

    def _looks_like_location(self, lowered: str) -> bool:
        if self._looks_like_desktop_control(lowered):
            return False
        if any(token in lowered for token in self._LOCATION_PATTERNS):
            return True
        return bool(re.search(r"\bwhere\s+is\b", lowered))

    def _looks_like_location_followup(self, lowered: str, previous_structured: dict[str, Any] | None) -> bool:
        if not self._has_location_context(previous_structured):
            return False
        if self._looks_like_desktop_control(lowered):
            return False
        if any(token in lowered for token in self._MAP_DISPLAY_PATTERNS):
            return True
        normalized = re.sub(r"[\s,.;:!?，。！？]+", "", lowered)
        if normalized in {"map", "\u5730\u56fe", "\u5730\u56fe\u5462"}:
            return True
        return "\u5730\u56fe" in lowered and any(
            token in lowered
            for token in (
                "\u663e\u793a",
                "\u5c55\u793a",
                "\u770b\u770b",
                "\u6253\u5f00",
                "\u9884\u89c8",
            )
        )

    def _looks_like_desktop_control(self, lowered: str) -> bool:
        has_control_verb = any(token in lowered for token in self._DESKTOP_CONTROL_VERBS)
        has_screen_target = any(token in lowered for token in self._SCREEN_REFERENTS)
        return has_control_verb and has_screen_target

    def _looks_like_news(self, lowered: str) -> bool:
        return any(token in lowered for token in self._NEWS_PATTERNS)

    def _has_location_context(self, previous_structured: dict[str, Any] | None) -> bool:
        payload = self._location_context_source(previous_structured)
        if not payload:
            return False
        lat = payload.get("lat")
        lon = payload.get("lon")
        if lat not in {None, ""} and lon not in {None, ""}:
            return True
        return any(
            str(payload.get(key, "") or "").strip()
            for key in ("map_url", "address", "title", "place_name", "image_url", "external_map_url", "map_preview_url")
        )

    def _extract_location_context(self, previous_structured: dict[str, Any] | None) -> dict[str, Any]:
        payload = self._location_context_source(previous_structured)
        if not self._has_location_context(payload):
            return {}
        context: dict[str, Any] = {}
        for key in (
            "title",
            "place_name",
            "address",
            "distance",
            "distance_text",
            "city",
            "region",
            "country",
            "lat",
            "lon",
            "map_url",
            "external_map_url",
            "image_url",
            "map_preview_url",
            "url",
        ):
            value = payload.get(key)
            if value not in {None, ""}:
                context[key] = value
        return context

    def _location_context_source(self, previous_structured: dict[str, Any] | None) -> dict[str, Any]:
        payload = previous_structured if isinstance(previous_structured, dict) else {}
        if not payload:
            return {}
        nested = payload.get("location")
        if isinstance(nested, dict) and nested:
            merged = dict(payload)
            merged.update(nested)
            return merged
        return payload
