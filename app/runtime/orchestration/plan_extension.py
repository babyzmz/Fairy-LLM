from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.query_resolution.slot_sources import get_configured_default_city

from .execution_plan import ExecutionStep
from .plan_state import ExecutionPlanState, StepExecutionResult


@dataclass(slots=True)
class PlanExtensionDecision:
    inserted_steps: list[ExecutionStep] = field(default_factory=list)
    updated_steps: list[ExecutionStep] = field(default_factory=list)
    cancelled_step_ids: list[str] = field(default_factory=list)
    reasoning: list[dict[str, Any]] = field(default_factory=list)


class PlanExtensionHook:
    def extend(
        self,
        *,
        original_message: str,
        step: ExecutionStep,
        result: StepExecutionResult,
        current_state: ExecutionPlanState,
        session_context: Any,
    ) -> PlanExtensionDecision:
        lowered = str(original_message or "").strip().lower()
        decision = PlanExtensionDecision()
        existing_capabilities = {item.capability for item in current_state.steps}

        self._rewrite_downstream_location_steps(step=step, result=result, current_state=current_state, decision=decision)
        self._insert_weather_step_if_needed(
            lowered=lowered,
            step=step,
            result=result,
            current_state=current_state,
            session_context=session_context,
            existing_capabilities=existing_capabilities,
            decision=decision,
        )
        self._insert_alternative_search_step_if_needed(
            step=step,
            result=result,
            current_state=current_state,
            decision=decision,
        )
        return decision

    def _rewrite_downstream_location_steps(
        self,
        *,
        step: ExecutionStep,
        result: StepExecutionResult,
        current_state: ExecutionPlanState,
        decision: PlanExtensionDecision,
    ) -> None:
        if step.capability not in {"location_lookup", "display_information"} or not result.success or not result.produced_entities:
            return
        location = result.produced_entities[0]
        for existing in current_state.steps:
            if existing.step_id == step.step_id:
                continue
            if existing.capability not in {"weather_lookup", "time_lookup"}:
                continue
            if str(existing.slots.get("location") or "").strip():
                continue
            updated_slots = dict(existing.slots)
            updated_slots["location"] = location
            if existing.capability == "weather_lookup":
                updated_query = f"weather in {location} today"
            else:
                updated_query = f"current time in {location}"
            decision.updated_steps.append(
                existing.clone_with(
                    slots=updated_slots,
                    normalized_query=updated_query,
                    rewrite_source="location_result_carryover",
                )
            )
            decision.reasoning.append(
                {
                    "step": existing.capability,
                    "why_rewritten": "location_result_carryover",
                    "source_step": step.step_id,
                    "location": location,
                }
            )

    def _insert_weather_step_if_needed(
        self,
        *,
        lowered: str,
        step: ExecutionStep,
        result: StepExecutionResult,
        current_state: ExecutionPlanState,
        session_context: Any,
        existing_capabilities: set[str],
        decision: PlanExtensionDecision,
    ) -> None:
        wants_weather = any(
            token in lowered
            for token in (
                "\u5929\u6c14",
                "weather",
                "forecast",
            )
        )
        if not wants_weather or "weather_lookup" in existing_capabilities:
            return
        if step.capability not in {"generic_search", "location_lookup", "display_information"} or not result.success:
            return
        location = (
            str(getattr(session_context, "active_weather_location", "") or "").strip()
            or str(getattr(session_context, "active_location_target", "") or "").strip()
            or str(getattr(session_context, "active_city", "") or "").strip()
            or (result.produced_entities[0] if result.produced_entities else "")
            or get_configured_default_city()
        )
        if not location:
            return
        decision.inserted_steps.append(
            ExecutionStep(
                capability="weather_lookup",
                slots={"location": location, "date": "today"},
                step_index=len(current_state.steps) + len(decision.inserted_steps),
                normalized_query=f"weather in {location} today",
                retryable=True,
                depends_on=[step.step_id],
                can_run_parallel=False,
                abort_group=step.abort_group or step.branch_key,
                branch_key=step.branch_key,
                dependency_mode="success",
                execution_confidence_score=0.78,
                inserted_by=step.step_id,
                rewrite_source="latent_weather_followup_intent",
            )
        )
        decision.reasoning.append(
            {
                "step": step.capability,
                "why_inserted": "latent_weather_followup_intent",
                "inserted_capability": "weather_lookup",
                "location": location,
            }
        )

    def _insert_alternative_search_step_if_needed(
        self,
        *,
        step: ExecutionStep,
        result: StepExecutionResult,
        current_state: ExecutionPlanState,
        decision: PlanExtensionDecision,
    ) -> None:
        if step.rewrite_source == "alternative_source_rewrite":
            return
        if step.capability not in {"generic_search", "news_lookup"}:
            return
        low_confidence = result.execution_confidence_score < 0.42
        failed = not result.success and (result.error_type or "") not in {"step_skipped", "missing_required_slots"}
        if not (low_confidence or failed):
            return
        fallback_query = self._fallback_query(step)
        if not fallback_query:
            return
        if any(
            existing.inserted_by == step.step_id and existing.rewrite_source == "alternative_source_rewrite"
            for existing in current_state.steps
        ):
            return
        inserted_capability = "generic_search" if step.capability == "news_lookup" else step.capability
        inserted_slots = {"query": fallback_query}
        if step.capability == "news_lookup":
            inserted_slots["topic"] = str(step.slots.get("topic") or fallback_query).strip()
        decision.inserted_steps.append(
            ExecutionStep(
                capability=inserted_capability,
                slots=inserted_slots,
                step_index=len(current_state.steps) + len(decision.inserted_steps),
                normalized_query=fallback_query,
                retryable=True,
                depends_on=[step.step_id],
                can_run_parallel=False,
                abort_group=step.abort_group or step.branch_key,
                branch_key=step.branch_key,
                dependency_mode="settled",
                execution_confidence_score=max(0.35, step.execution_confidence_score - 0.1),
                inserted_by=step.step_id,
                rewrite_source="alternative_source_rewrite",
            )
        )
        decision.reasoning.append(
            {
                "step": step.capability,
                "why_inserted": "alternative_source_rewrite",
                "inserted_capability": inserted_capability,
                "fallback_query": fallback_query,
                "confidence": result.execution_confidence_score,
            }
        )

    @staticmethod
    def _fallback_query(step: ExecutionStep) -> str:
        query = str(step.slots.get("query") or "").strip()
        topic = str(step.slots.get("topic") or "").strip()
        location = str(step.slots.get("location") or "").strip()
        base = query or topic or location or str(step.normalized_query or "").strip()
        if not base:
            return ""
        replacements = (
            "\u4fbf\u5b9c\u7684",
            "\u6700\u4fbf\u5b9c\u7684",
            "\u503c\u5f97\u4e70\u5417",
            "\u987a\u4fbf",
            "\u4eca\u5929",
            "\u73b0\u5728",
            "\u5e2e\u6211",
            "\u7ed9\u6211",
            "\u67e5\u4e00\u4e0b",
            "\u67e5",
            "\u770b\u770b",
        )
        cleaned = base
        for token in replacements:
            cleaned = cleaned.replace(token, " ")
        cleaned = " ".join(cleaned.split()).strip()
        return cleaned or base
