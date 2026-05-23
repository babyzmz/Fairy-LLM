from __future__ import annotations

from .rule_models import RuleArbitrationContext, RulePostValidationResult


class RulePostValidator:
    _TIME_TERMS = ("几点", "时间", "当地时间", "current time", "what time", "local time", "time")
    _WEATHER_TERMS = ("天气", "温度", "气温", "weather", "forecast", "下雨")
    _MAP_TERMS = ("地图", "map", "显示地图", "看看地图", "打开地图")
    _LOCATION_TERMS = ("在哪里", "在哪", "where is", "地址", "位置", "地点", "导航到")

    def validate(
        self,
        *,
        normalized_text: str,
        selected_capability: str,
        context: RuleArbitrationContext,
    ) -> RulePostValidationResult:
        lowered = str(normalized_text or "").strip().lower()
        selected = str(selected_capability or "").strip()
        forced = str(context.forced_capability or "").strip()

        if forced and forced != selected:
            return RulePostValidationResult(
                final_capability=forced,
                correction_applied=True,
                correction_reason="forced_capability_rule_override",
            )

        if self._contains_any(lowered, self._TIME_TERMS) and selected != "time_lookup":
            return RulePostValidationResult(
                final_capability="time_lookup",
                correction_applied=True,
                correction_reason="time_phrase_post_validation",
            )

        if self._contains_any(lowered, self._WEATHER_TERMS) and selected != "weather_lookup":
            return RulePostValidationResult(
                final_capability="weather_lookup",
                correction_applied=True,
                correction_reason="weather_phrase_post_validation",
            )

        if context.slot_overrides.get("location") and (
            self._contains_any(lowered, self._MAP_TERMS) or self._contains_any(lowered, self._LOCATION_TERMS)
        ) and selected != "location_lookup":
            return RulePostValidationResult(
                final_capability="location_lookup",
                correction_applied=True,
                correction_reason="explicit_location_direct_path_post_validation",
            )

        if self._contains_any(lowered, self._MAP_TERMS) and selected not in {"display_information", "location_lookup"}:
            return RulePostValidationResult(
                final_capability="display_information",
                correction_applied=True,
                correction_reason="map_phrase_post_validation",
            )

        if self._contains_any(lowered, self._LOCATION_TERMS) and selected not in {
            "location_lookup",
            "display_information",
            "time_lookup",
            "weather_lookup",
        }:
            return RulePostValidationResult(
                final_capability="location_lookup",
                correction_applied=True,
                correction_reason="location_phrase_post_validation",
            )

        return RulePostValidationResult(final_capability=selected)

    @staticmethod
    def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
        return any(token in text for token in tokens)
