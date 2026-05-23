from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RuleArbitrationContext:
    matched_rules: list[str] = field(default_factory=list)
    forced_capability: str = ""
    capability_boosts: dict[str, float] = field(default_factory=dict)
    slot_overrides: dict[str, str] = field(default_factory=dict)
    inferred_followup_type: str = ""
    sticky_action: str = "keep"
    unstick_reasons: list[str] = field(default_factory=list)
    strict_override_slots: set[str] = field(default_factory=set)
    allow_sticky_bonus: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched_rules": list(self.matched_rules),
            "forced_capability": self.forced_capability,
            "capability_boosts": dict(self.capability_boosts),
            "slot_overrides": dict(self.slot_overrides),
            "inferred_followup_type": self.inferred_followup_type,
            "sticky_action": self.sticky_action,
            "unstick_reasons": list(self.unstick_reasons),
            "strict_override_slots": sorted(self.strict_override_slots),
            "allow_sticky_bonus": self.allow_sticky_bonus,
        }


@dataclass(slots=True)
class RulePostValidationResult:
    final_capability: str
    correction_applied: bool = False
    correction_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_capability": self.final_capability,
            "correction_applied": self.correction_applied,
            "correction_reason": self.correction_reason,
        }
