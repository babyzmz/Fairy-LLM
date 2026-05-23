from __future__ import annotations

from .rule_models import RuleArbitrationContext


def apply_arbitration_rules(context: RuleArbitrationContext) -> RuleArbitrationContext:
    if context.forced_capability:
        marker = f"forced_capability.{context.forced_capability}"
        if marker not in context.matched_rules:
            context.matched_rules.append(marker)
        context.allow_sticky_bonus = False
    if context.sticky_action in {"switch", "unstick"} and "sticky.unstick" not in context.matched_rules:
        context.matched_rules.append("sticky.unstick")
    return context
