from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CapabilityArbitrationResult:
    selected_capability: str
    rejected_candidates: list[str] = field(default_factory=list)
    arbitration_reason: list[str] = field(default_factory=list)
    llm_capability_candidate: str = ""


class CapabilityArbitrator:
    def select(
        self,
        *,
        evaluations: list[dict[str, Any]],
        intent_hint: str,
        followup_target: str,
        session_context: Any,
        allow_sticky_bonus: bool = True,
        forced_capability: str = "",
        llm_capability_candidate: str = "",
    ) -> CapabilityArbitrationResult:
        if not evaluations:
            return CapabilityArbitrationResult(selected_capability="generic_search", arbitration_reason=["no_candidates"])

        scored: list[tuple[float, dict[str, Any]]] = []
        last_capability = str(getattr(session_context, "last_capability", "") or "").strip()
        forced = str(forced_capability or "").strip()

        for item in evaluations:
            candidate = item["candidate"]
            resolution = item["resolution"]
            semantic = item["semantic"]
            score = float(candidate.score) + float(semantic.adjusted_score)
            score += 0.04 * len(list((resolution.validated_slots or {}).keys()))
            score -= 0.22 * len(list(resolution.missing_slots or []))
            if not resolution.clarification_needed:
                score += 0.05
            if candidate.capability == intent_hint:
                score += 0.05
            if followup_target == "weather" and candidate.capability == "weather_lookup":
                score += 0.08
            if followup_target == "location" and candidate.capability in {"location_lookup", "display_information", "time_lookup"}:
                score += 0.08
            if allow_sticky_bonus and last_capability == candidate.capability:
                score += 0.04
            if forced and candidate.capability == forced:
                score += 0.30
            if forced == "location_lookup" and candidate.capability == "display_information":
                score -= 0.20
            item["final_score"] = round(score, 4)
            scored.append((score, item))

        scored.sort(
            key=lambda pair: (
                -pair[0],
                pair[1]["resolution"].clarification_needed,
                len(pair[1]["resolution"].missing_slots),
                pair[1]["candidate"].capability,
            )
        )
        selected = scored[0][1]
        selected_capability = str(selected["candidate"].capability)
        llm_choice = str(llm_capability_candidate or "").strip()

        if llm_choice and not forced and any(item["candidate"].capability == llm_choice for _score, item in scored):
            selected_capability = llm_choice
            selected = next(item for _score, item in scored if item["candidate"].capability == llm_choice)

        rejected = [item["candidate"].capability for _score, item in scored if item["candidate"].capability != selected_capability]
        reasons = [f"selected:{selected_capability}:score={selected['final_score']}"]
        semantic_reason = str(selected["semantic"].reason or "").strip()
        if semantic_reason:
            reasons.append(semantic_reason)
        if forced:
            reasons.append(f"forced:{forced}")
        if llm_choice:
            reasons.append(f"llm_candidate:{llm_choice}")
        if not allow_sticky_bonus:
            reasons.append("sticky_bonus_disabled")
        for _score, item in scored:
            capability = item["candidate"].capability
            if capability == selected_capability:
                continue
            reasons.append(f"rejected:{capability}:score={item['final_score']}")

        return CapabilityArbitrationResult(
            selected_capability=selected_capability,
            rejected_candidates=rejected,
            arbitration_reason=reasons,
            llm_capability_candidate=llm_choice,
        )
