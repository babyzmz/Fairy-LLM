from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class SlotNormalizationResult:
    raw_value: str
    normalized_value: str
    confidence: float
    changed: bool = False


class SlotNormalizer:
    _DIRECT_LOCATION_PATTERNS = (
        re.compile(r"^(?:显示|打开|看看|看一看|查看)?\s*(?P<location>.+?)\s*地图$", re.IGNORECASE),
        re.compile(r"^(?P<location>.+?)\s*(?:在哪里|在哪儿|在哪)$", re.IGNORECASE),
        re.compile(r"^(?:导航到|导航去|前往)\s*(?P<location>.+)$", re.IGNORECASE),
        re.compile(r"^(?:看看|看一看|查看)\s*(?P<location>.+?)\s*位置$", re.IGNORECASE),
        re.compile(r"^(?:show|display|open)\s+map\s+for\s+(?P<location>.+)$", re.IGNORECASE),
        re.compile(r"^(?:where\s+is)\s+(?P<location>.+)$", re.IGNORECASE),
    )
    _LOCATION_ALIASES = {
        "chengdu": "成都",
        "成都": "成都",
        "成都市": "成都",
        "melbourne": "墨尔本",
        "墨尔本": "墨尔本",
        "墨尔本市": "墨尔本",
        "new york": "纽约",
        "new york city": "纽约",
        "nyc": "纽约",
        "纽约": "纽约",
        "阿德莱德": "阿德莱德",
        "adelaide": "阿德莱德",
    }
    _DATE_ALIASES = {
        "今天": "today",
        "今晚": "today",
        "today": "today",
        "tonight": "today",
        "明天": "tomorrow",
        "tomorrow": "tomorrow",
        "后天": "tomorrow",
        "昨天": "yesterday",
        "yesterday": "yesterday",
    }
    _TOPIC_ALIASES = {
        "ai": "人工智能",
        "ai 新闻": "人工智能",
        "人工智能": "人工智能",
        "人工智能新闻": "人工智能",
        "科技": "科技",
        "科技新闻": "科技",
        "tech": "科技",
        "technology": "科技",
    }
    _QUERY_PREFIXES = (
        "帮我查一下",
        "给我查一下",
        "帮我查",
        "帮我找",
        "查一下",
        "查",
        "搜一下",
        "搜",
        "看看",
        "有没有",
        "哪个好",
        "解释一下",
        "解释",
        "你能不能解释一下",
    )

    def normalize(self, *, slot_name: str, value: str, capability: str) -> SlotNormalizationResult:
        raw = str(value or "").strip()
        if not raw:
            return SlotNormalizationResult(raw_value="", normalized_value="", confidence=0.0)
        if slot_name == "location":
            normalized = self._normalize_location(raw)
        elif slot_name == "date":
            normalized = self._normalize_date(raw)
        elif slot_name == "topic":
            normalized = self._normalize_topic(raw)
        elif slot_name == "query":
            normalized = self._normalize_query(raw, capability=capability)
        else:
            normalized = raw
        return SlotNormalizationResult(
            raw_value=raw,
            normalized_value=normalized,
            confidence=0.9 if normalized else 0.0,
            changed=normalized != raw,
        )

    def _normalize_location(self, value: str) -> str:
        cleaned = str(value or "").strip()
        direct_match = self._extract_direct_location_target(cleaned)
        if direct_match:
            cleaned = direct_match
        cleaned = re.sub(r"^(帮我看看|给我看看|看看|查一下|查|搜一下|搜)\s*", "", cleaned).strip()
        cleaned = re.sub(r"^(那里的|那里|那边的|那边|这里的|这里)\s*", "", cleaned).strip()
        cleaned = re.sub(r"^(里的|这边的|那边的)\s*", "", cleaned).strip()
        cleaned = re.sub(r"^(的)\s*", "", cleaned).strip()
        cleaned = re.sub(r"^(现在)\s*", "", cleaned).strip()
        cleaned = re.sub(r"(现在几点了|几点了|几点|当地时间|时间)$", "", cleaned).strip()
        cleaned = re.sub(r"(现在)$", "", cleaned).strip()
        cleaned = re.sub(r"(天气怎么样|天气如何|天气多少|天气呢|天气)$", "", cleaned).strip()
        cleaned = re.sub(r"(在哪里|在哪儿|在哪个州|在哪|在中国吗|地址)$", "", cleaned).strip()
        cleaned = re.sub(r"(呢|吗|呀|啊)$", "", cleaned).strip()
        lowered = cleaned.lower()
        alias = self._LOCATION_ALIASES.get(lowered) or self._LOCATION_ALIASES.get(cleaned)
        if alias:
            return alias
        cleaned = re.sub(r"(特别行政区|自治区|省|市|区|县)$", "", cleaned).strip()
        if cleaned in {"那里", "那边", "这里", "这个地方", "那个地方", "天气", "地图", "现在", "时间"}:
            return ""
        return cleaned

    def _extract_direct_location_target(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        for pattern in self._DIRECT_LOCATION_PATTERNS:
            match = pattern.match(text)
            if not match:
                continue
            candidate = str(match.group("location") or "").strip()
            if candidate:
                return candidate
        return ""

    def _normalize_date(self, value: str) -> str:
        cleaned = str(value or "").strip()
        lowered = cleaned.lower()
        return self._DATE_ALIASES.get(cleaned) or self._DATE_ALIASES.get(lowered) or cleaned

    def _normalize_topic(self, value: str) -> str:
        cleaned = str(value or "").strip().strip("，。！？? ")
        lowered = cleaned.lower()
        alias = self._TOPIC_ALIASES.get(lowered) or self._TOPIC_ALIASES.get(cleaned)
        if alias:
            return alias
        cleaned = re.sub(r"(新闻|资讯)$", "", cleaned).strip()
        return cleaned

    def _normalize_query(self, value: str, *, capability: str) -> str:
        cleaned = str(value or "").strip()
        for prefix in self._QUERY_PREFIXES:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :].strip()
                break
        cleaned = re.sub(r"^(那|这个|那个)\s*", "", cleaned).strip()
        if capability == "explanation":
            cleaned = re.sub(r"^(什么是|是什么|是什么意思|为什么|怎么回事)\s*", "", cleaned).strip()
        cleaned = re.sub(r"(呢|呀|啊)$", "", cleaned).strip()
        return cleaned.strip("，。！？? ")
