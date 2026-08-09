from __future__ import annotations

from dataclasses import dataclass

from fairy_core.assistant.interpretation import (
    ClassifierInterpretationPayload,
    InterpretationDisposition,
    RequestAction,
)


@dataclass(frozen=True, slots=True)
class RequestIntentPolicyResult:
    disposition: InterpretationDisposition
    missing_information: tuple[str, ...]
    clarification_question: str | None


def apply_request_intent_policy(
    payload: ClassifierInterpretationPayload,
) -> RequestIntentPolicyResult:
    """Narrow classifier output when Core-owned action requirements are incomplete."""

    missing = list(payload.missing_information)
    if payload.action in {
        RequestAction.CHANGE,
        RequestAction.RUN,
        RequestAction.MANAGE,
    } and not payload.targets:
        missing.append("an explicit target")
    if payload.action in {
        RequestAction.CREATE,
        RequestAction.GENERATE,
    } and payload.deliverable is None and not payload.targets:
        missing.append("the requested deliverable")
    if (
        payload.action is RequestAction.SCHEDULE
        and payload.deliverable is None
        and not payload.targets
    ):
        missing.append("the scheduled task and timing")

    normalized_missing = tuple(dict.fromkeys(missing))
    if normalized_missing:
        return RequestIntentPolicyResult(
            disposition=InterpretationDisposition.CLARIFICATION_REQUIRED,
            missing_information=normalized_missing,
            clarification_question=(
                payload.clarification_question
                or _clarification_question(normalized_missing)
            ),
        )
    if payload.assumptions and payload.disposition is InterpretationDisposition.READY:
        return RequestIntentPolicyResult(
            disposition=InterpretationDisposition.ASSUMED,
            missing_information=(),
            clarification_question=None,
        )
    return RequestIntentPolicyResult(
        disposition=payload.disposition,
        missing_information=(),
        clarification_question=payload.clarification_question,
    )


def _clarification_question(missing: tuple[str, ...]) -> str:
    visible = ", ".join(missing[:3])
    return f"Please clarify {visible} before Fairy continues."


__all__ = ["RequestIntentPolicyResult", "apply_request_intent_policy"]
