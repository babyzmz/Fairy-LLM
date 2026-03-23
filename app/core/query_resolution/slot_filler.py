from __future__ import annotations

import re
from typing import Any

from app.core.perception.perception_models import DetectedEntity

from .capability_contracts import CapabilitySlotContract
from .slot_sources import get_configured_default_city


class SlotFiller:
    _GENERIC_LOCATION_VALUES = {
        "今天",
        "明天",
        "昨天",
        "现在",
        "现在几点",
        "几点",
        "时间",
        "当地时间",
        "里的时间",
        "那里的时间",
        "查",
        "天气",
        "地图",
        "显示地图",
        "给我看看地图",
        "把地图打开看看",
        "看看地图",
        "show map",
        "display map",
        "open map",
    }
    _GENERIC_TOPIC_VALUES = {"查", "看看", "帮我查", "解释一下", "解释", "新闻"}
    _NEWS_GENERIC_PREFIXES = ("查", "看看", "帮我查", "来点", "给我看", "新闻", "今天", "最新")

    def extract_explicit_slots(
        self,
        *,
        normalized_text: str,
        capability: str,
        entities: list[DetectedEntity],
        session_context: Any,
    ) -> tuple[dict[str, str], dict[str, str]]:
        slots: dict[str, str] = {}
        sources: dict[str, str] = {}
        entity_map = self._entity_map(entities)

        if capability in {"weather_lookup", "time_lookup", "news_lookup"} and entity_map.get("date"):
            slots["date"] = self._normalize_date(entity_map["date"])
            sources["date"] = "explicit_date"

        if (
            capability in {"weather_lookup", "time_lookup", "location_lookup", "display_information"}
            and entity_map.get("location")
            and not self._is_generic_location(entity_map["location"])
        ):
            slots["location"] = entity_map["location"]
            sources["location"] = "explicit_location"

        if capability in {"news_lookup", "generic_search", "explanation"} and entity_map.get("topic"):
            normalized_topic = self._normalize_topic_candidate(
                entity_map["topic"],
                capability=capability,
                text=normalized_text,
            )
            if normalized_topic:
                slots["topic"] = normalized_topic
                sources["topic"] = "explicit_topic"

        if capability == "system_action":
            action_name = self._derive_system_action(normalized_text)
            if action_name:
                slots["action_name"] = action_name
                sources["action_name"] = "action_keyword"

        if capability == "generic_search":
            query_value = self._derive_search_query(normalized_text)
            if query_value:
                slots["query"] = query_value
                sources["query"] = "normalized_query"

        if capability == "explanation":
            topic_value = self._derive_explanation_topic(normalized_text)
            if topic_value:
                slots["topic"] = topic_value
                sources["topic"] = "explicit_topic"

        topic_keyword = self._derive_topic_from_text(normalized_text)
        if topic_keyword and "topic" not in slots and capability in {"news_lookup", "generic_search"}:
            slots["topic"] = topic_keyword
            sources["topic"] = "topic_keyword"

        active_topic = str(getattr(session_context, "active_topic", "") or "").strip()
        if capability == "explanation" and not slots.get("context_entity") and active_topic and not self._is_non_specific_explanation_topic(active_topic):
            slots["context_entity"] = active_topic
            sources["context_entity"] = "followup_context"

        return slots, sources

    def apply_default_sources(
        self,
        *,
        contract: CapabilitySlotContract,
        normalized_text: str,
        slots: dict[str, str],
        slot_sources: dict[str, str],
        session_context: Any,
        previous_structured: dict[str, Any] | None,
        followup_target: str,
        followup_focus_value: str,
    ) -> tuple[dict[str, str], dict[str, str], list[dict[str, str]]]:
        applied_defaults: list[dict[str, str]] = []
        for slot_name, source_names in contract.default_slot_sources.items():
            if slots.get(slot_name):
                continue
            for source_name in source_names:
                value = self._resolve_source_value(
                    source_name,
                    slot_name=slot_name,
                    normalized_text=normalized_text,
                    session_context=session_context,
                    previous_structured=previous_structured or {},
                    followup_target=followup_target,
                    followup_focus_value=followup_focus_value,
                )
                if not value:
                    continue
                slots[slot_name] = value
                slot_sources[slot_name] = source_name
                applied_defaults.append({"slot": slot_name, "value": value, "source": source_name})
                break
        return slots, slot_sources, applied_defaults

    def missing_required_slots(self, contract: CapabilitySlotContract, slots: dict[str, str]) -> list[str]:
        return [slot for slot in contract.required_slots if not str(slots.get(slot) or "").strip()]

    def _resolve_source_value(
        self,
        source_name: str,
        *,
        slot_name: str,
        normalized_text: str,
        session_context: Any,
        previous_structured: dict[str, Any],
        followup_target: str,
        followup_focus_value: str,
    ) -> str:
        if source_name == "followup_context":
            if followup_focus_value and followup_target in {"location", "weather"} and slot_name in {"location", "topic"}:
                candidate = str(followup_focus_value).strip()
                if slot_name == "location" and self._is_generic_location(candidate):
                    return ""
                return candidate
            if slot_name == "topic":
                return str(
                    getattr(session_context, "active_topic", "")
                    or getattr(session_context, "last_explanation_topic", "")
                    or getattr(session_context, "last_generic_query", "")
                    or ""
                ).strip()
        if source_name == "session_last_weather_location":
            return str(getattr(session_context, "active_weather_location", "") or "").strip()
        if source_name == "session_last_location":
            return str(getattr(session_context, "active_location_target", "") or "").strip()
        if source_name == "session_last_city":
            return str(getattr(session_context, "active_city", "") or "").strip()
        if source_name == "previous_weather_card":
            return self._from_previous(
                previous_structured,
                ("weather.city", "weather.weather_location", "city", "weather_location", "title"),
            )
        if source_name == "previous_location_card":
            return self._from_previous(previous_structured, ("location.title", "location.city", "title", "city", "address"))
        if source_name == "configured_default_city":
            return get_configured_default_city()
        if source_name == "default_today":
            return "today"
        if source_name == "topic_keyword":
            return self._derive_topic_from_text(normalized_text)
        if source_name == "normalized_query":
            return self._derive_search_query(normalized_text)
        if source_name == "previous_card_topic":
            topics = list(getattr(session_context, "last_card_topics", []) or [])
            return str(topics[0] if topics else "").strip()
        if source_name == "previous_user_query_focus":
            return str(
                getattr(session_context, "last_generic_query", "")
                or getattr(session_context, "last_explanation_topic", "")
                or getattr(session_context, "active_topic", "")
                or ""
            ).strip()
        if source_name == "current_ui_context":
            return "main_window"
        return ""

    def _entity_map(self, entities: list[DetectedEntity]) -> dict[str, str]:
        values: dict[str, str] = {}
        for entity in entities:
            if entity.kind not in values and entity.value:
                values[entity.kind] = str(entity.value).strip()
        return values

    def _normalize_date(self, value: str) -> str:
        lowered = str(value or "").strip().lower()
        if lowered in {"today", "今天", "今晚"}:
            return "today"
        if lowered in {"tomorrow", "明天", "后天"}:
            return "tomorrow"
        if lowered in {"yesterday", "昨天"}:
            return "yesterday"
        return lowered or "today"

    def _is_generic_location(self, value: str) -> bool:
        cleaned = str(value or "").strip().lower()
        return not cleaned or cleaned in {item.lower() for item in self._GENERIC_LOCATION_VALUES}

    def _is_generic_topic(self, value: str) -> bool:
        cleaned = str(value or "").strip().lower()
        return not cleaned or cleaned in {item.lower() for item in self._GENERIC_TOPIC_VALUES}

    def _normalize_topic_candidate(self, value: str, *, capability: str, text: str) -> str:
        topic = str(value or "").strip()
        if self._is_generic_topic(topic):
            return ""
        if capability == "news_lookup":
            return self.normalize_news_topic(text, topic)
        if capability == "generic_search":
            derived = self._derive_topic_from_text(text)
            return derived if derived else ""
        if capability == "explanation":
            return self._derive_explanation_topic(text) or topic
        return topic

    def _derive_topic_from_text(self, text: str) -> str:
        lowered = str(text or "").strip().lower()
        topic_map = (
            ("AI", (" ai ", "ai新闻", "ai news", "人工智能")),
            ("科技", ("科技", "tech", "technology")),
            ("财经", ("财经", "finance", "financial", "business", "market")),
            ("体育", ("体育", "sports", "football", "soccer", "nba")),
            ("国际", ("国际", "world", "global", "international")),
        )
        padded = f" {lowered} "
        for topic, keywords in topic_map:
            if any(keyword in padded or keyword in lowered for keyword in keywords):
                return topic
        return ""

    def normalize_news_topic(self, text: str, current_topic: str) -> str:
        topic = str(current_topic or "").strip()
        cleaned = str(text or "").strip()
        if not topic:
            return ""
        if topic == cleaned:
            derived = self._derive_topic_from_text(cleaned)
            return derived or ""
        for token in self._NEWS_GENERIC_PREFIXES:
            topic = topic.replace(token, " ").strip()
        topic = re.sub(r"\b(news|latest)\b", " ", topic, flags=re.IGNORECASE)
        topic = " ".join(topic.split()).strip("，。！？? ")
        if not topic:
            return ""
        derived = self._derive_topic_from_text(topic)
        normalized = derived or topic
        if normalized in {"新闻", "查新闻"}:
            return ""
        return normalized

    def _derive_search_query(self, text: str) -> str:
        cleaned = str(text or "").strip()
        cleaned = re.sub(r"^(请|麻烦|给我|帮我)+", "", cleaned).strip()
        prefixes = (
            "帮我查一下",
            "给我查一下",
            "帮我查",
            "帮我找",
            "查一下",
            "搜一下",
            "搜",
            "查",
            "看看",
            "有没有",
            "哪个好",
        )
        for prefix in prefixes:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :].strip()
                break
        return cleaned

    def _derive_explanation_topic(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if cleaned in {"解释", "解释一下", "一下"}:
            return ""
        patterns = (
            r"^(?P<topic>.+?)\s*是什么(?:意思)?$",
            r"^(?P<topic>.+?)\s*是什么意思$",
            r"^解释一下\s*(?P<topic>.+)$",
            r"^解释\s*(?P<topic>.+)$",
            r"^为什么\s*(?P<topic>.+)$",
            r"^怎么回事\s*(?P<topic>.+)$",
            r"^那和\s*(?P<topic>.+?)\s*有什么关系$",
            r"^what is\s+(?P<topic>.+)$",
            r"^explain\s+(?P<topic>.+)$",
        )
        for pattern in patterns:
            match = re.match(pattern, cleaned, flags=re.IGNORECASE)
            if match:
                topic = str(match.group("topic") or "").strip("，。！？? ")
                return "" if self._is_non_specific_explanation_topic(topic) else topic
        return "" if self._is_non_specific_explanation_topic(cleaned) else cleaned

    def _derive_system_action(self, text: str) -> str:
        lowered = str(text or "").strip().lower()
        mapping = (
            ("refresh_capabilities", ("刷新能力", "刷新功能列表", "refresh capabilities")),
            ("clear_asset_cache", ("清理缓存", "清除缓存", "clear cache", "clear asset cache")),
            ("restart_backend", ("重启后端", "重启服务", "restart backend")),
            ("open_panel", ("打开系统面板", "打开调试面板", "open system panel", "open debug panel")),
            ("focus_window", ("聚焦窗口", "切到主窗口", "focus window")),
            ("show_notification", ("显示通知", "弹出通知", "show notification")),
            ("reveal_asset_folder", ("打开资源目录", "打开缓存目录", "reveal asset folder")),
        )
        for action_name, keys in mapping:
            if any(key.lower() in lowered for key in keys):
                return action_name
        return ""

    def _from_previous(self, payload: dict[str, Any], keys: tuple[str, ...]) -> str:
        for key in keys:
            current: Any = payload
            for part in key.split("."):
                if not isinstance(current, dict):
                    current = None
                    break
                current = current.get(part)
            value = str(current or "").strip()
            if value:
                return value
        return ""

    def _is_non_specific_explanation_topic(self, value: str) -> bool:
        cleaned = str(value or "").strip().lower()
        return cleaned in {"解释", "解释一下", "一下", "这个", "那个", "this", "that"}
