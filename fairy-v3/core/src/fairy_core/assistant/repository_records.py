from __future__ import annotations

from fairy_core.assistant.models import ProviderAttempt


def provider_attempt_values(attempt: ProviderAttempt) -> dict[str, object]:
    return {
        "id": str(attempt.id),
        "turn_id": str(attempt.turn_id),
        "task_id": str(attempt.task_id),
        "model_round": attempt.model_round,
        "attempt_number": attempt.attempt_number,
        "profile_id": attempt.profile_id,
        "model_id": attempt.model_id,
        "endpoint_kind": attempt.endpoint_kind.value,
        "model_role": attempt.model_role.value,
        "status": attempt.status.value,
        "error_category": (
            attempt.error_category.value if attempt.error_category is not None else None
        ),
        "usage": dict(attempt.usage),
        "usage_cost": attempt.usage_cost,
        "created_at": attempt.created_at,
        "completed_at": attempt.completed_at,
    }


__all__ = ["provider_attempt_values"]
