from __future__ import annotations

from .capability_contracts import CapabilitySlotContract
from .resolution_models import QueryResolutionResult, ResolutionTraceEvent


class ClarificationPolicy:
    def apply(self, resolution: QueryResolutionResult, contract: CapabilitySlotContract | None) -> QueryResolutionResult:
        if not resolution.missing_slots or contract is None:
            return resolution

        first_missing = resolution.missing_slots[0]
        message = str(contract.clarification_messages.get(first_missing) or "").strip()
        if not message:
            return resolution

        resolution.clarification_needed = True
        resolution.clarification_title = "需要更多信息"
        resolution.clarification_message = message
        resolution.trace.append(
            ResolutionTraceEvent(
                stage="missing_required_slot",
                text=f"Missing required slot: {first_missing}",
                data={"slot": first_missing, "capability": contract.capability},
            )
        )
        resolution.trace.append(
            ResolutionTraceEvent(
                stage="clarification_required",
                text=message,
                data={"slot": first_missing, "capability": contract.capability},
            )
        )
        return resolution
