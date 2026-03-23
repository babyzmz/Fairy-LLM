from __future__ import annotations

from app.core.perception.perception_models import ModalityPreference, PerceptionFrame


class ModalityDetector:
    def detect(self, frame: PerceptionFrame) -> tuple[ModalityPreference, ...]:
        if frame.intent in {"control", "system_action"}:
            return ("progress", "text")
        if frame.intent in {"research", "news_lookup"}:
            return ("progress", "text", "card", "speech")
        if frame.intent in {"lookup", "weather_lookup", "time_lookup", "location_lookup", "display_information"}:
            if frame.has_entity("location"):
                return ("text", "card", "speech", "progress")
            if frame.intent in {"location_lookup", "display_information"}:
                return ("text", "card", "speech", "progress")
            if frame.intent == "time_lookup":
                return ("text", "card", "speech", "progress")
            return ("text", "card", "speech")
        if frame.intent == "explanation":
            return ("text", "speech")
        return ("text", "speech")
