from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.perception.perception_models import DetectedEntity


@dataclass(slots=True)
class CapabilityCandidate:
    capability: str
    score: float
    evidence: list[str] = field(default_factory=list)
    extracted_slots: dict[str, Any] = field(default_factory=dict)
    semantic_flags: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "score": round(float(self.score), 4),
            "evidence": list(self.evidence),
            "extracted_slots": dict(self.extracted_slots),
            "semantic_flags": dict(self.semantic_flags),
        }


class CapabilityCandidateGenerator:
    _TIME_TERMS = (
        "几点",
        "现在几点",
        "时间",
        "当地时间",
        "current time",
        "what time",
        "time",
    )
    _WEATHER_TERMS = ("天气", "气温", "温度", "预报", "weather", "forecast", "下雨", "冷不冷")
    _LOCATION_TERMS = (
        "在哪里",
        "在哪",
        "where is",
        "地址",
        "位置",
        "地点",
        "在中国吗",
        "哪个州",
        "哪个国家",
        "属于哪里",
    )
    _MAP_TERMS = ("地图", "map", "显示地图", "看看地图", "打开地图")
    _NEWS_TERMS = ("新闻", "news", "快讯", "头条", "资讯")
    _EXPLANATION_TERMS = ("解释", "是什么", "什么意思", "why", "explain", "怎么回事", "关系")
    _SEARCH_TERMS = ("查", "搜", "帮我查", "帮我找", "看看", "有没有", "值不值得买", "价格", "功耗", "性能")
    _SYSTEM_TERMS = ("重启后端", "刷新能力", "清理缓存", "open panel", "restart backend")

    def generate(
        self,
        *,
        normalized_text: str,
        intent_hint: str,
        entities: list[DetectedEntity],
        session_context: Any,
        followup_target: str = "",
    ) -> list[CapabilityCandidate]:
        lowered = str(normalized_text or "").strip().lower()
        entity_map = self._entity_map(entities)
        last_capability = str(getattr(session_context, "last_capability", "") or "").strip()
        clarification_target = str(getattr(session_context, "last_clarification_target", "") or "").strip()
        candidates: dict[str, CapabilityCandidate] = {}

        def add(
            capability: str,
            score: float,
            *,
            evidence: str,
            extracted_slots: dict[str, Any] | None = None,
            semantic_flags: dict[str, Any] | None = None,
        ) -> None:
            existing = candidates.get(capability)
            if existing is None or score > existing.score:
                candidates[capability] = CapabilityCandidate(
                    capability=capability,
                    score=score,
                    evidence=[evidence],
                    extracted_slots=dict(extracted_slots or {}),
                    semantic_flags=dict(semantic_flags or {}),
                )
                return
            existing.score = max(existing.score, score)
            if evidence not in existing.evidence:
                existing.evidence.append(evidence)
            existing.extracted_slots.update(dict(extracted_slots or {}))
            existing.semantic_flags.update(dict(semantic_flags or {}))

        if clarification_target and self._looks_like_slot_value_only(normalized_text):
            add(clarification_target, 1.04, evidence="clarification_target")

        if intent_hint:
            add(intent_hint, 0.72, evidence=f"intent_hint:{intent_hint}")

        if self._contains_any(lowered, self._TIME_TERMS):
            add("time_lookup", 0.96, evidence="time_phrase", extracted_slots=self._location_slots(entity_map))
            if entity_map.get("location"):
                add("location_lookup", 0.32, evidence="location_entity_with_time_phrase", extracted_slots=self._location_slots(entity_map))

        if self._contains_any(lowered, self._WEATHER_TERMS):
            add("weather_lookup", 0.95, evidence="weather_phrase", extracted_slots=self._location_slots(entity_map))
            if entity_map.get("location"):
                add("location_lookup", 0.28, evidence="location_entity_with_weather_phrase", extracted_slots=self._location_slots(entity_map))

        if self._contains_any(lowered, self._MAP_TERMS):
            add("display_information", 0.95, evidence="map_phrase", extracted_slots=self._location_slots(entity_map))

        if self._contains_any(lowered, self._LOCATION_TERMS):
            add("location_lookup", 0.93, evidence="location_phrase", extracted_slots=self._location_slots(entity_map))

        if self._contains_any(lowered, self._NEWS_TERMS):
            topic = entity_map.get("topic") or ""
            add("news_lookup", 0.92, evidence="news_phrase", extracted_slots={"topic": topic} if topic else {})

        if self._contains_any(lowered, self._EXPLANATION_TERMS):
            topic = entity_map.get("topic") or ""
            add("explanation", 0.88, evidence="explanation_phrase", extracted_slots={"topic": topic} if topic else {})

        if self._contains_any(lowered, self._SYSTEM_TERMS):
            add("system_action", 0.94, evidence="system_action_phrase")

        if self._contains_any(lowered, self._SEARCH_TERMS):
            add("generic_search", 0.74, evidence="search_phrase", extracted_slots={"query": normalized_text})

        if followup_target == "weather":
            add("weather_lookup", 0.82, evidence="followup_target:weather", extracted_slots=self._location_slots(entity_map))
            if self._contains_any(lowered, self._TIME_TERMS):
                add("time_lookup", 0.86, evidence="followup_target:weather_with_time", extracted_slots=self._location_slots(entity_map))
        if followup_target == "location":
            add("location_lookup", 0.82, evidence="followup_target:location", extracted_slots=self._location_slots(entity_map))
            if self._contains_any(lowered, self._TIME_TERMS):
                add("time_lookup", 0.88, evidence="followup_target:location_with_time", extracted_slots=self._location_slots(entity_map))
            if self._contains_any(lowered, self._MAP_TERMS):
                add("display_information", 0.9, evidence="followup_target:location_map", extracted_slots=self._location_slots(entity_map))

        if last_capability == "explanation" and self._contains_any(lowered, ("关系", "difference", "embedding", "why", "解释")):
            add("explanation", 0.84, evidence="last_capability:explanation")
        if last_capability == "generic_search" and self._contains_any(lowered, ("价格", "price", "功耗", "性能", "值不值得买")):
            add("generic_search", 0.84, evidence="last_capability:generic_search")

        if not candidates:
            add("generic_search", 0.5, evidence="fallback_generic_search", extracted_slots={"query": normalized_text})

        ordered = sorted(candidates.values(), key=lambda item: (-item.score, item.capability))
        return ordered[:3]

    @staticmethod
    def _contains_any(lowered: str, tokens: tuple[str, ...]) -> bool:
        return any(token in lowered for token in tokens)

    @staticmethod
    def _looks_like_slot_value_only(text: str) -> bool:
        cleaned = str(text or "").strip()
        return bool(cleaned) and len(cleaned) <= 24 and all(token not in cleaned for token in ("天气", "地图", "新闻", "解释"))

    @staticmethod
    def _entity_map(entities: list[DetectedEntity]) -> dict[str, str]:
        values: dict[str, str] = {}
        for entity in entities:
            if entity.kind not in values and entity.value:
                values[entity.kind] = str(entity.value).strip()
        return values

    @staticmethod
    def _location_slots(entity_map: dict[str, str]) -> dict[str, Any]:
        location = str(entity_map.get("location") or "").strip()
        return {"location": location} if location else {}
