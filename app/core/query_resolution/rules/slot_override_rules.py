from __future__ import annotations

from .rule_models import RuleArbitrationContext


def apply_slot_override_rules(context: RuleArbitrationContext) -> RuleArbitrationContext:
    location = str(context.slot_overrides.get("location") or "").strip()
    if location:
        if "slot_override.location" not in context.matched_rules:
            context.matched_rules.append("slot_override.location")
        context.strict_override_slots.add("location")
    return context
