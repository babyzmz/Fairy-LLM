from __future__ import annotations

from .rule_models import RuleArbitrationContext


_FOLLOWUP_MAP = {
    "weather": "weather_followup",
    "location": "location_followup",
}


def apply_followup_rules(*, followup_target: str, followup_focus_value: str, context: RuleArbitrationContext) -> RuleArbitrationContext:
    target = str(followup_target or "").strip().lower()
    focus = str(followup_focus_value or "").strip()
    if not target:
        return context

    marker = f"followup.target.{target}"
    if marker not in context.matched_rules:
        context.matched_rules.append(marker)
    if not context.inferred_followup_type:
        context.inferred_followup_type = _FOLLOWUP_MAP.get(target, "followup")

    if focus and not context.slot_overrides.get("location") and target in {"weather", "location"}:
        context.slot_overrides["location"] = focus
    return context
