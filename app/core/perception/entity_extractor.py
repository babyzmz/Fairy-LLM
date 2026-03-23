from __future__ import annotations

import re

from app.core.perception.perception_models import DetectedEntity


class EntityExtractor:
    _DATE_PATTERNS = (
        "today",
        "tomorrow",
        "yesterday",
        "tonight",
        "今天",
        "今晚",
        "明天",
        "后天",
        "本周",
        "下周",
    )
    _LOCATION_PATTERNS = (
        re.compile(r"(?:where is|where's)\s+(?P<value>.+)$", flags=re.IGNORECASE),
        re.compile(r"(?P<value>.+?)(?:在哪里|在哪儿|在哪)$"),
        re.compile(r"(?:地址|位置|地点|附近|最近的?)\s*(?P<value>.+)$"),
        re.compile(r"(?P<value>.+?)(?:天气怎么样|天气如何|天气呢|天气)$"),
        re.compile(r"(?P<value>.+?)(?:现在几点了|几点了|当地时间|时间)$"),
        re.compile(r"(?:现在)?\s*(?P<value>.+?)\s*(?:几点|时间)$"),
        re.compile(r"(?:current time|what time|local time)\s+(?:in\s+)?(?P<value>.+)$", flags=re.IGNORECASE),
        re.compile(r"(?:weather|forecast)\s+(?:in\s+)?(?P<value>.+)$", flags=re.IGNORECASE),
        re.compile(
            r"^(?:那|那么|那边|那儿|那里)?(?P<value>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z路街区县市州省国\-\s]{1,24}?)(?:呢|怎么样|如何)?\??$",
            flags=re.IGNORECASE,
        ),
    )
    _PERSON_PATTERN = re.compile(r"(?:who is|关于)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)")
    _TOPIC_STOPWORDS = (
        "天气怎么样",
        "天气如何",
        "天气呢",
        "天气",
        "在哪里",
        "在哪儿",
        "在哪",
        "显示地图",
        "给我看看地图",
        "今天科技新闻",
    )

    def extract(self, normalized_text: str) -> list[DetectedEntity]:
        text = str(normalized_text or "").strip()
        lowered = text.lower()
        entities: list[DetectedEntity] = []

        for token in self._DATE_PATTERNS:
            if token in lowered or token in text:
                entities.append(DetectedEntity(kind="date", value=token, confidence=0.88, source_text=token))
                break

        location = self._extract_location(text)
        if location:
            entities.append(DetectedEntity(kind="location", value=location, confidence=0.9, source_text=location))

        person = self._extract_person(text)
        if person:
            entities.append(DetectedEntity(kind="person", value=person, confidence=0.74, source_text=person))

        topic = self._extract_topic(text, location=location, person=person)
        if topic:
            entities.append(DetectedEntity(kind="topic", value=topic, confidence=0.62, source_text=topic))
        return entities

    def _extract_location(self, text: str) -> str:
        lowered = text.lower()
        for pattern in self._LOCATION_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            candidate = self._clean_fragment(match.group("value"))
            if candidate:
                return candidate
        if any(token in lowered for token in ("地图", "地址", "位置", "where is", "nearby", "nearest", "邮局", "时间", "几点", "what time", "current time", "local time")):
            return self._clean_fragment(text)
        return ""

    def _extract_person(self, text: str) -> str:
        match = self._PERSON_PATTERN.search(text)
        if match:
            return match.group(1).strip()
        return ""

    def _extract_topic(self, text: str, *, location: str = "", person: str = "") -> str:
        topic = text
        for token in (location, person):
            if token:
                topic = topic.replace(token, " ")
        for token in self._TOPIC_STOPWORDS:
            topic = topic.replace(token, " ")
        topic = self._clean_fragment(" ".join(topic.split()))
        if len(topic) < 2:
            return ""
        return topic[:80]

    def _clean_fragment(self, value: str) -> str:
        cleaned = str(value or "").strip().strip("，。！？? ")
        cleaned = re.sub(r"^(?:那|那么|那边|那儿|那里|请问)\s*", "", cleaned)
        cleaned = re.sub(r"(?:天气怎么样|天气如何|天气呢|天气|在哪里|在哪儿|在哪|呢|怎么样|如何)$", "", cleaned).strip()
        return cleaned.strip("，。！？? ")
