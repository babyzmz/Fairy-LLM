from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SlotValidationResult:
    valid: bool
    normalized_value: str | None = None
    reason: str | None = None
    requires_clarification: bool = False


class SlotValidator:
    _VALID_DATES = {"today", "tomorrow", "yesterday"}
    _INVALID_LOCATIONS = {"今天", "明天", "查", "显示地图", "那天气呢", "天气", "地图"}
    _INVALID_TOPICS = {"查新闻", "新闻", "解释一下", "解释", "一下"}
    _INVALID_QUERIES = {"查", "看看", "这个", "那个"}

    def validate(self, *, slot_name: str, value: str, capability: str) -> SlotValidationResult:
        cleaned = str(value or "").strip()
        if not cleaned:
            return SlotValidationResult(valid=False, reason="empty", requires_clarification=True)
        if slot_name == "location":
            return self._validate_location(cleaned)
        if slot_name == "date":
            return self._validate_date(cleaned)
        if slot_name == "topic":
            return self._validate_topic(cleaned, capability=capability)
        if slot_name == "query":
            return self._validate_query(cleaned)
        return SlotValidationResult(valid=True, normalized_value=cleaned)

    def _validate_location(self, value: str) -> SlotValidationResult:
        if len(value.strip()) <= 1:
            return SlotValidationResult(valid=False, reason="location_too_short", requires_clarification=True)
        if value in self._INVALID_LOCATIONS or value in {"的", "里的"}:
            return SlotValidationResult(valid=False, reason="generic_location_token", requires_clarification=True)
        if any(token in value for token in ("天气", "地图", "新闻", "解释", "时间", "几点")):
            return SlotValidationResult(valid=False, reason="query_fragment_location", requires_clarification=True)
        return SlotValidationResult(valid=True, normalized_value=value)

    def _validate_date(self, value: str) -> SlotValidationResult:
        if value not in self._VALID_DATES:
            return SlotValidationResult(valid=False, reason="unsupported_date_token", requires_clarification=False)
        return SlotValidationResult(valid=True, normalized_value=value)

    def _validate_topic(self, value: str, *, capability: str) -> SlotValidationResult:
        if value in self._INVALID_TOPICS:
            return SlotValidationResult(valid=False, reason="generic_topic_token", requires_clarification=True)
        if capability == "news_lookup" and value.endswith("新闻") and len(value) <= 4:
            return SlotValidationResult(valid=False, reason="generic_news_topic", requires_clarification=True)
        return SlotValidationResult(valid=True, normalized_value=value)

    def _validate_query(self, value: str) -> SlotValidationResult:
        if value in self._INVALID_QUERIES:
            return SlotValidationResult(valid=False, reason="generic_query_token", requires_clarification=True)
        return SlotValidationResult(valid=True, normalized_value=value)
