from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CapabilityArbitrationResult:
    selected_capability: str
    rejected_candidates: list[str] = field(default_factory=list)
    arbitration_reason: list[str] = field(default_factory=list)


class CapabilityArbitrator:
    def select(
        self,
        *,
        evaluations: list[dict[str, Any]],
        intent_hint: str,
        followup_target: str,
        session_context: Any,
    ) -> CapabilityArbitrationResult:
        if not evaluations:
            return CapabilityArbitrationResult(selected_capability="generic_search", arbitration_reason=["no_candidates"])

        scored: list[tuple[float, dict[str, Any]]] = []
        last_capability = str(getattr(session_context, "last_capability", "") or "").strip()
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
            if last_capability == candidate.capability:
                score += 0.04
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
        rejected = [item["candidate"].capability for _score, item in scored[1:]]
        reasons = [f"selected:{selected['candidate'].capability}:score={selected['final_score']}"]
        semantic_reason = str(selected["semantic"].reason or "").strip()
        if semantic_reason:
            reasons.append(semantic_reason)
        for _score, item in scored[1:]:
            reasons.append(f"rejected:{item['candidate'].capability}:score={item['final_score']}")
        return CapabilityArbitrationResult(
            selected_capability=selected["candidate"].capability,
            rejected_candidates=rejected,
            arbitration_reason=reasons,
        )
