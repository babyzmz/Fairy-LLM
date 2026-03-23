from __future__ import annotations

from typing import Any

from app.agent.perception.perception_models import PerceptionFrame


class EntityFocusTracker:
    def update_focus(self, context, frame: PerceptionFrame, *, structured: dict[str, Any] | None = None) -> None:
        context.last_capability = str(frame.intent or "").strip()
        resolution = dict(frame.metadata.get("resolution") or {})
        resolved_slots = dict(resolution.get("validated_slots") or resolution.get("slots") or {})
        for entity in frame.entities:
            if entity.kind == "location":
                context.active_location_target = entity.value
                context.active_city = entity.value
                if frame.intent == "weather_lookup":
                    context.active_weather_location = entity.value
            elif entity.kind == "topic":
                context.active_topic = entity.value
                if frame.intent == "explanation":
                    context.last_explanation_topic = entity.value
        payload = structured if isinstance(structured, dict) else {}
        nested = payload.get("location") if isinstance(payload.get("location"), dict) else {}
        nested_weather = payload.get("weather") if isinstance(payload.get("weather"), dict) else {}
        source = nested or payload
        location_target = str(source.get("title") or source.get("address") or "").strip()
        if location_target:
            context.active_location_target = location_target
        weather_source = nested_weather or payload
        weather_location = str(
            weather_source.get("city")
            or weather_source.get("weather_location")
            or weather_source.get("title")
            or ""
        ).strip()
        if weather_location:
            context.active_weather_location = weather_location
            context.active_city = weather_location
        if frame.intent == "generic_search":
            context.last_generic_query = str(resolved_slots.get("query") or frame.normalized_text).strip()
            context.active_topic = str(
                resolved_slots.get("topic")
                or resolved_slots.get("query")
                or context.active_topic
                or ""
            ).strip()
        if frame.intent == "explanation":
            explanation_topic = str(resolved_slots.get("topic") or context.active_topic or "").strip()
            if explanation_topic:
                context.last_explanation_topic = explanation_topic
                context.active_topic = explanation_topic
        if payload:
            context.last_structured = dict(payload)
            if isinstance(payload.get("routing"), dict):
                context.last_structured_type = str(payload["routing"].get("primary_intent", "") or context.last_structured_type)
            extracted_topics: list[str] = []
            for key in ("topic", "title", "headline", "name"):
                value = str(payload.get(key) or "").strip()
                if value:
                    extracted_topics.append(value)
            if extracted_topics:
                context.last_card_topics = extracted_topics[:4]
        if context.last_clarification_target and frame.intent == context.last_clarification_target:
            context.last_clarification_target = ""
            context.last_clarification_message = ""
