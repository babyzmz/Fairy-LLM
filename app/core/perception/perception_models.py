from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


PerceptionIntent = Literal[
    "factual",
    "lookup",
    "research",
    "explanation",
    "control",
    "weather_lookup",
    "time_lookup",
    "location_lookup",
    "news_lookup",
    "generic_search",
    "display_information",
    "system_action",
]
EntityKind = Literal["location", "date", "person", "topic"]
ModalityPreference = Literal["text", "card", "speech", "progress"]


@dataclass(slots=True)
class DetectedEntity:
    kind: EntityKind
    value: str
    confidence: float
    source_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PerceptionFrame:
    raw_text: str
    normalized_text: str
    intent: PerceptionIntent
    entities: list[DetectedEntity]
    confidence: float
    preferred_modalities: tuple[ModalityPreference, ...]
    followup_target: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def first_entity(self, kind: EntityKind | None = None) -> DetectedEntity | None:
        for entity in self.entities:
            if kind is None or entity.kind == kind:
                return entity
        return None

    def has_entity(self, kind: EntityKind) -> bool:
        return self.first_entity(kind) is not None
