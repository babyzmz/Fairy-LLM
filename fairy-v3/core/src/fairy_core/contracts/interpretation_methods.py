from __future__ import annotations

from fairy_core.contracts.method_primitives import CoreMethod
from fairy_core.contracts.models import (
    AssistantRequestInterpretationModel,
    AssistantTurnInterpretationInput,
    AssistantTurnModel,
    AssistantTurnRespondInput,
)

INTERPRETATION_METHODS = {
    "assistant.turns.interpretation.get": CoreMethod(
        "assistant.turns.interpretation.get",
        AssistantTurnInterpretationInput,
        AssistantRequestInterpretationModel,
    ),
    "assistant.turns.respond": CoreMethod(
        "assistant.turns.respond",
        AssistantTurnRespondInput,
        AssistantTurnModel,
    ),
}

__all__ = ["INTERPRETATION_METHODS"]
