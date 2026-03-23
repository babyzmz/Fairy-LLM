from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SemanticValidationResult:
    consistent: bool
    adjusted_score: float
    reason: str | None = None
    suggested_capability: str | None = None
    flags: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "consistent": self.consistent,
            "adjusted_score": round(float(self.adjusted_score), 4),
            "reason": self.reason,
            "suggested_capability": self.suggested_capability,
            "flags": dict(self.flags),
        }


class SemanticSlotValidator:
    _TIME_TERMS = ("几点", "时间", "当地时间", "current time", "what time", "time")
    _WEATHER_TERMS = ("天气", "温度", "气温", "weather", "forecast", "下雨", "冷不冷")
    _LOCATION_TERMS = ("在哪里", "在哪", "where is", "地址", "位置", "地点", "在中国吗", "哪个州", "哪个国家")
    _MAP_TERMS = ("地图", "map", "显示地图", "看看地图", "打开地图")
    _NEWS_TERMS = ("新闻", "news", "快讯", "头条")
    _EXPLANATION_TERMS = ("解释", "是什么", "什么意思", "why", "explain", "关系", "difference")

    def validate(
        self,
        *,
        capability: str,
        normalized_text: str,
        slots: dict[str, Any],
        followup_target: str = "",
    ) -> SemanticValidationResult:
        lowered = str(normalized_text or "").strip().lower()
        flags = {
            "has_time_phrase": self._contains_any(lowered, self._TIME_TERMS),
            "has_weather_phrase": self._contains_any(lowered, self._WEATHER_TERMS),
            "has_location_phrase": self._contains_any(lowered, self._LOCATION_TERMS),
            "has_map_phrase": self._contains_any(lowered, self._MAP_TERMS),
            "has_news_phrase": self._contains_any(lowered, self._NEWS_TERMS),
            "has_explanation_phrase": self._contains_any(lowered, self._EXPLANATION_TERMS),
            "has_location_slot": bool(str(slots.get("location") or "").strip()),
        }

        if capability == "time_lookup":
            if flags["has_time_phrase"]:
                return SemanticValidationResult(True, 0.62, "time_phrase_supports_time_lookup", flags=flags)
            if followup_target in {"location", "weather"} and flags["has_location_slot"]:
                return SemanticValidationResult(True, 0.24, "followup_location_supports_time_lookup", flags=flags)
            if flags["has_weather_phrase"]:
                return SemanticValidationResult(False, -0.55, "weather_phrase_conflicts_time_lookup", "weather_lookup", flags)
            return SemanticValidationResult(True, -0.08, "weak_time_signal", flags=flags)

        if capability == "weather_lookup":
            if flags["has_time_phrase"]:
                return SemanticValidationResult(False, -0.92, "time_phrase_conflicts_weather_lookup", "time_lookup", flags)
            if flags["has_weather_phrase"]:
                return SemanticValidationResult(True, 0.56, "weather_phrase_supports_weather_lookup", flags=flags)
            if flags["has_location_phrase"]:
                return SemanticValidationResult(False, -0.36, "location_phrase_weaker_than_weather", "location_lookup", flags)
            return SemanticValidationResult(True, -0.08, "weak_weather_signal", flags=flags)

        if capability == "location_lookup":
            if flags["has_time_phrase"]:
                return SemanticValidationResult(False, -0.96, "time_phrase_conflicts_location_lookup", "time_lookup", flags)
            if flags["has_weather_phrase"]:
                return SemanticValidationResult(False, -0.84, "weather_phrase_conflicts_location_lookup", "weather_lookup", flags)
            if flags["has_location_phrase"] or flags["has_map_phrase"]:
                return SemanticValidationResult(True, 0.58, "location_phrase_supports_location_lookup", flags=flags)
            return SemanticValidationResult(True, -0.12, "weak_location_signal", flags=flags)

        if capability == "display_information":
            if flags["has_time_phrase"]:
                return SemanticValidationResult(False, -0.88, "time_phrase_conflicts_map_display", "time_lookup", flags)
            if flags["has_map_phrase"]:
                return SemanticValidationResult(True, 0.64, "map_phrase_supports_display_information", flags=flags)
            if flags["has_location_phrase"]:
                return SemanticValidationResult(True, 0.18, "location_phrase_supports_display_information", flags=flags)
            return SemanticValidationResult(True, -0.1, "weak_map_signal", flags=flags)

        if capability == "news_lookup":
            if flags["has_news_phrase"]:
                return SemanticValidationResult(True, 0.54, "news_phrase_supports_news_lookup", flags=flags)
            if flags["has_weather_phrase"] or flags["has_time_phrase"]:
                return SemanticValidationResult(False, -0.42, "non_news_phrase_conflicts_news_lookup", "generic_search", flags)
            return SemanticValidationResult(True, -0.06, "weak_news_signal", flags=flags)

        if capability == "explanation":
            if flags["has_explanation_phrase"]:
                return SemanticValidationResult(True, 0.46, "explanation_phrase_supports_explanation", flags=flags)
            if flags["has_time_phrase"]:
                return SemanticValidationResult(False, -0.42, "time_phrase_conflicts_explanation", "time_lookup", flags)
            if flags["has_weather_phrase"]:
                return SemanticValidationResult(False, -0.4, "weather_phrase_conflicts_explanation", "weather_lookup", flags)
            return SemanticValidationResult(True, -0.04, "weak_explanation_signal", flags=flags)

        if capability == "generic_search":
            if flags["has_time_phrase"] or flags["has_weather_phrase"] or flags["has_location_phrase"] or flags["has_news_phrase"]:
                return SemanticValidationResult(True, -0.34, "specific_capability_available_generic_search_penalized", flags=flags)
            return SemanticValidationResult(True, 0.08, "generic_search_fallback", flags=flags)

        if capability == "system_action":
            return SemanticValidationResult(True, 0.12, "system_action_candidate", flags=flags)

        return SemanticValidationResult(True, 0.0, "no_semantic_adjustment", flags=flags)

    @staticmethod
    def _contains_any(lowered: str, tokens: tuple[str, ...]) -> bool:
        return any(token in lowered for token in tokens)
