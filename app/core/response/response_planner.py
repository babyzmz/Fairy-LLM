from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.perception.perception_models import PerceptionFrame
from app.core.response.execution_plan import ExecutionPlan, ExecutionStep
from app.core.response.modality_planner import ModalityPlan, ModalityPlanner
from app.response.models import ResponseRequestPlan


@dataclass(slots=True)
class PlannedResponse:
    perception: PerceptionFrame
    modality_plan: ModalityPlan
    request_plan: ResponseRequestPlan
    execution_plan: ExecutionPlan


class ResponsePlanner:
    def __init__(self, modality_planner: ModalityPlanner | None = None) -> None:
        self._modality_planner = modality_planner or ModalityPlanner()

    def plan(self, frame: PerceptionFrame, *, previous_structured: dict[str, Any] | None = None) -> PlannedResponse:
        modality = self._modality_planner.plan(frame)
        request_plan = ResponseRequestPlan(
            intent=self._legacy_intent(frame, modality),
            modality=modality.response_mode,  # type: ignore[arg-type]
            speech_mode=self._legacy_speech_mode(modality.speech_mode),  # type: ignore[arg-type]
            allow_voice_streaming=modality.allow_streaming,
            planner_confidence=frame.confidence,
            force_card_type=(modality.card_types[0] if modality.card_types else ""),
            context_payload=dict(previous_structured or {}),
        )
        execution_plan = ExecutionPlan(
            intent=request_plan.intent,
            capability=self._resolve_capability(frame),
            response_mode=modality.response_mode,
            speech_mode=modality.speech_mode,
            layout_mode=modality.layout_mode,  # type: ignore[arg-type]
            card_types=list(modality.card_types),
            steps=self._steps_for(frame, modality),
            use_tools=frame.intent in {"weather_lookup", "time_lookup", "location_lookup", "display_information", "news_lookup", "system_action"},
            allow_streaming=modality.allow_streaming,
            fallback_rules=self._fallback_rules(frame, modality),
        )
        return PlannedResponse(
            perception=frame,
            modality_plan=modality,
            request_plan=request_plan,
            execution_plan=execution_plan,
        )

    def _resolve_capability(self, frame: PerceptionFrame) -> str:
        if frame.intent == "system_action":
            return "system_action"
        if frame.intent in {"weather_lookup", "time_lookup", "location_lookup", "display_information", "news_lookup"}:
            return "web_research"
        if frame.intent == "explanation":
            return "explanation"
        if frame.intent == "generic_search":
            return "generic_search"
        return "direct_answer"

    def _legacy_intent(self, frame: PerceptionFrame, modality: ModalityPlan) -> str:
        if frame.intent == "weather_lookup":
            return "weather"
        if frame.intent == "time_lookup":
            return "time"
        if frame.intent in {"location_lookup", "display_information"} or (modality.card_types and modality.card_types[0] == "location"):
            return "location"
        if frame.intent == "news_lookup" or (modality.card_types and modality.card_types[0] == "news_list"):
            return "news"
        if frame.intent == "explanation":
            return "explanation"
        if frame.intent == "generic_search":
            return "generic_search"
        if frame.intent == "system_action":
            return "system_action"
        return "text"

    def _legacy_speech_mode(self, speech_mode: str) -> str:
        if speech_mode in {"concise_structured", "summary_first", "detailed_explainer"}:
            return speech_mode
        return "detailed_explainer"

    def _steps_for(self, frame: PerceptionFrame, modality: ModalityPlan) -> list[ExecutionStep]:
        steps: list[ExecutionStep] = [
            ExecutionStep(name="perception", description="Normalize input and detect intent"),
        ]
        if frame.intent in {"weather_lookup", "time_lookup", "location_lookup", "display_information", "news_lookup"}:
            steps.append(
                ExecutionStep(
                    name="search_web",
                    description="Run structured lookup or retrieval tools",
                    tool_name="search_web",
                    blocking=True,
                )
            )
        if frame.intent == "system_action":
            steps.append(
                ExecutionStep(
                    name="system_action_dispatch",
                    description="Dispatch backend or desktop system action",
                    tool_name="system_bridge",
                    blocking=True,
                )
            )
        steps.append(
            ExecutionStep(
                name="summarize",
                description="Assemble user-facing summary",
                allows_streaming=modality.allow_streaming,
            )
        )
        if modality.card_types:
            steps.append(ExecutionStep(name="render_cards", description="Render structured cards"))
        if modality.speech_mode != "silent":
            steps.append(ExecutionStep(name="speak_summary", description="Queue speech output"))
        return steps

    def _fallback_rules(self, frame: PerceptionFrame, modality: ModalityPlan) -> list[str]:
        rules = ["fallback_to_text_bubble", "fallback_to_generic_info_card"]
        if frame.intent in {"location_lookup", "display_information"} or "location" in modality.card_types:
            rules.append("reuse_last_location_context")
        if frame.intent == "weather_lookup":
            rules.append("reuse_last_weather_context")
        if frame.intent == "time_lookup":
            rules.append("reuse_last_location_context")
        if "news_list" in modality.card_types:
            rules.append("emit_progress_before_full_card_set")
        return rules
