from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .execution_plan import ExecutionStep
from .plan_state import StepExecutionResult


@dataclass(slots=True)
class PlanExtensionDecision:
    inserted_steps: list[ExecutionStep] = field(default_factory=list)
    reasoning: list[dict[str, Any]] = field(default_factory=list)


class PlanExtensionHook:
    def extend(
        self,
        *,
        original_message: str,
        step: ExecutionStep,
        result: StepExecutionResult,
        current_steps: list[ExecutionStep],
    ) -> PlanExtensionDecision:
        lowered = str(original_message or "").strip().lower()
        decision = PlanExtensionDecision()
        existing_capabilities = {item.capability for item in current_steps}

        if (
            step.capability == "location_lookup"
            and result.success
            and result.produced_entities
            and "weather_lookup" not in existing_capabilities
            and any(token in lowered for token in ("\u5929\u6c14", "weather"))
        ):
            location = result.produced_entities[0]
            decision.inserted_steps.append(
                ExecutionStep(
                    capability="weather_lookup",
                    slots={"location": location, "date": "today"},
                    step_index=len(current_steps) + len(decision.inserted_steps),
                    normalized_query=f"weather in {location} today",
                    retryable=True,
                )
            )
            decision.reasoning.append(
                {
                    "step": step.capability,
                    "why_inserted": "location_success_with_weather_intent",
                    "inserted_capability": "weather_lookup",
                }
            )

        if (
            step.capability == "weather_lookup"
            and result.success
            and "news_lookup" not in existing_capabilities
            and any(token in lowered for token in ("\u987a\u4fbf", "\u518d\u770b\u770b", "\u65b0\u95fb", "news"))
            and any(token in lowered for token in ("\u79d1\u6280", "ai", "tech", "\u4eba\u5de5\u667a\u80fd"))
        ):
            topic = "\u4eba\u5de5\u667a\u80fd" if ("ai" in lowered or "\u4eba\u5de5\u667a\u80fd" in lowered) else "\u79d1\u6280"
            decision.inserted_steps.append(
                ExecutionStep(
                    capability="news_lookup",
                    slots={"topic": topic, "date": "today"},
                    step_index=len(current_steps) + len(decision.inserted_steps),
                    normalized_query=f"{topic} news today",
                    retryable=True,
                )
            )
            decision.reasoning.append(
                {
                    "step": step.capability,
                    "why_inserted": "weather_success_with_followup_news_intent",
                    "inserted_capability": "news_lookup",
                }
            )

        return decision
