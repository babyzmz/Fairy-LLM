from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CapabilityIntent:
    capability: str
    slots: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    normalized_query: str = ""
    requires_clarification: bool = False
    missing_slots: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "slots": dict(self.slots),
            "confidence": self.confidence,
            "normalized_query": self.normalized_query,
            "requires_clarification": self.requires_clarification,
            "missing_slots": list(self.missing_slots),
        }


@dataclass(slots=True)
class ResolutionTraceEvent:
    stage: str
    text: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {"stage": self.stage, "text": self.text}
        if self.data:
            payload["data"] = dict(self.data)
        return payload


@dataclass(slots=True)
class QueryResolutionResult:
    capability: str
    normalized_query: str
    intent_hint: str
    execution_mode: str = "single"
    plan_continuation: bool = False
    continuation_plan_id: str = ""
    primary_intent: CapabilityIntent | None = None
    secondary_intents: list[CapabilityIntent] = field(default_factory=list)
    resolved_slots: dict[str, str] = field(default_factory=dict)
    normalized_slots: dict[str, str] = field(default_factory=dict)
    validated_slots: dict[str, str] = field(default_factory=dict)
    rejected_slots: dict[str, dict[str, Any]] = field(default_factory=dict)
    missing_slots: list[str] = field(default_factory=list)
    slot_sources: dict[str, str] = field(default_factory=dict)
    carryover_trace: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    selected_capability: str = ""
    rejected_candidates: list[str] = field(default_factory=list)
    semantic_validation: dict[str, Any] = field(default_factory=dict)
    arbitration_reason: list[str] = field(default_factory=list)
    clarification_needed: bool = False
    clarification_message: str | None = None
    clarification_title: str | None = None
    route_hints: dict[str, Any] = field(default_factory=dict)
    trace: list[ResolutionTraceEvent] = field(default_factory=list)
    matched_rules: list[str] = field(default_factory=list)
    inferred_followup_type: str = ""
    sticky_capability_before: str = ""
    sticky_capability_after: str = ""
    slot_override_applied: bool = False
    llm_capability_candidate: str = ""
    final_capability_decision: str = ""
    arbitration_correction_applied: bool = False

    def to_meta(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "intent_guess": self.intent_hint,
            "execution_mode": self.execution_mode,
            "plan_continuation": self.plan_continuation,
            "continuation_plan_id": self.continuation_plan_id,
            "primary_intent": self.primary_intent.to_dict() if self.primary_intent else {},
            "secondary_intents": [item.to_dict() for item in self.secondary_intents],
            "slots": dict(self.resolved_slots),
            "normalized_slots": dict(self.normalized_slots),
            "validated_slots": dict(self.validated_slots),
            "rejected_slots": dict(self.rejected_slots),
            "missing_slots": list(self.missing_slots),
            "slot_sources": dict(self.slot_sources),
            "carryover_trace": list(self.carryover_trace),
            "candidates": list(self.candidates),
            "selected_capability": self.selected_capability or self.capability,
            "rejected_candidates": list(self.rejected_candidates),
            "semantic_validation": dict(self.semantic_validation),
            "arbitration_reason": list(self.arbitration_reason),
            "clarification_needed": self.clarification_needed,
            "clarification_message": self.clarification_message,
            "normalized_query": self.normalized_query,
            "route_hints": dict(self.route_hints),
            "trace": [item.to_dict() for item in self.trace],
            "matched_rules": list(self.matched_rules),
            "inferred_followup_type": self.inferred_followup_type,
            "sticky_capability_before": self.sticky_capability_before,
            "sticky_capability_after": self.sticky_capability_after,
            "slot_override_applied": self.slot_override_applied,
            "llm_capability_candidate": self.llm_capability_candidate,
            "final_capability_decision": self.final_capability_decision or self.selected_capability or self.capability,
            "arbitration_correction_applied": self.arbitration_correction_applied,
        }
