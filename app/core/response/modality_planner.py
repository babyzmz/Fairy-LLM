from __future__ import annotations

from dataclasses import dataclass, field

from app.core.perception.perception_models import PerceptionFrame


@dataclass(slots=True)
class ModalityPlan:
    response_mode: str
    speech_mode: str
    layout_mode: str
    card_types: list[str] = field(default_factory=list)
    allow_streaming: bool = False


class ModalityPlanner:
    def plan(self, frame: PerceptionFrame) -> ModalityPlan:
        if frame.intent == "weather_lookup":
            return ModalityPlan(
                response_mode="text_plus_card",
                speech_mode="concise_structured",
                layout_mode="single",
                card_types=["weather"],
                allow_streaming=False,
            )
        if frame.intent == "time_lookup":
            return ModalityPlan(
                response_mode="text_plus_card",
                speech_mode="concise_structured",
                layout_mode="single",
                card_types=["time"],
                allow_streaming=False,
            )
        if frame.intent in {"location_lookup", "display_information"}:
            return ModalityPlan(
                response_mode="card_primary_text_summary",
                speech_mode="concise_structured",
                layout_mode="single",
                card_types=["location"],
                allow_streaming=False,
            )
        if frame.intent == "news_lookup":
            return ModalityPlan(
                response_mode="text_plus_card",
                speech_mode="summary_first",
                layout_mode="masonry",
                card_types=["news_list"],
                allow_streaming=False,
            )
        if frame.intent == "explanation":
            return ModalityPlan(
                response_mode="text_only",
                speech_mode="detailed_explainer",
                layout_mode="single",
                allow_streaming=True,
            )
        if frame.intent in {"control", "system_action"}:
            return ModalityPlan(
                response_mode="text_only",
                speech_mode="silent",
                layout_mode="single",
                allow_streaming=False,
            )
        return ModalityPlan(
            response_mode="text_only",
            speech_mode="detailed_explainer",
            layout_mode="single",
            allow_streaming=True,
        )
