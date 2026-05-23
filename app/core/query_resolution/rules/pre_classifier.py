from __future__ import annotations

from typing import Any

from .arbitration_rules import apply_arbitration_rules
from .followup_rules import apply_followup_rules
from .intent_rules import apply_intent_rules
from .rule_models import RuleArbitrationContext
from .slot_override_rules import apply_slot_override_rules


class RulePreClassifier:
    def classify(
        self,
        *,
        normalized_text: str,
        entities: list[Any],
        followup_target: str,
        followup_focus_value: str,
        session_context: Any,
    ) -> RuleArbitrationContext:
        context = apply_intent_rules(
            normalized_text=normalized_text,
            entities=entities,
            followup_target=followup_target,
            session_context=session_context,
        )
        context = apply_followup_rules(
            followup_target=followup_target,
            followup_focus_value=followup_focus_value,
            context=context,
        )
        context = apply_slot_override_rules(context)
        context = apply_arbitration_rules(context)
        if not context.inferred_followup_type and followup_target:
            context.inferred_followup_type = f"followup_{followup_target}"
        return context
