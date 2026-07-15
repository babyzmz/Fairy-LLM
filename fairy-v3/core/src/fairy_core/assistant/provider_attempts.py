from __future__ import annotations

from uuid import UUID

from fairy_core.assistant.models import ProviderAttempt
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import (
    ProviderAttemptEvent,
    ProviderAttemptStatus,
    ProviderErrorCategory,
)


class ProviderAttemptRecorder:
    def __init__(self, unit_of_work_factory: CoreUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def observer(self, turn_id: UUID, model_round: int):
        attempts: dict[int, ProviderAttempt] = {}
        with self._unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.assistant.list_provider_attempts(turn_id)
            round_attempts = tuple(
                attempt for attempt in existing if attempt.model_round == model_round
            )
            interrupted = False
            for attempt in round_attempts:
                if attempt.status is ProviderAttemptStatus.STARTED:
                    attempt.fail(
                        error_category=ProviderErrorCategory.UNKNOWN,
                        usage=dict(attempt.usage),
                        usage_cost=attempt.usage_cost,
                    )
                    unit_of_work.assistant.update_provider_attempt(attempt)
                    interrupted = True
            if interrupted:
                unit_of_work.commit()
        attempt_offset = max(
            (attempt.attempt_number for attempt in round_attempts),
            default=0,
        )

        def observe(event: ProviderAttemptEvent) -> None:
            if event.status is ProviderAttemptStatus.STARTED:
                with self._unit_of_work_factory() as unit_of_work:
                    turn = unit_of_work.assistant.get_turn(turn_id)
                    if turn is None:
                        raise KeyError(f"Assistant Turn not found: {turn_id}")
                    attempt = ProviderAttempt.create(
                        turn=turn,
                        model_round=model_round,
                        attempt_number=attempt_offset + event.attempt_number,
                        profile_id=event.profile_id,
                        model_id=event.model_id,
                        endpoint_kind=event.endpoint_kind,
                        model_role=event.model_role,
                    )
                    unit_of_work.assistant.save_provider_attempt(attempt)
                    unit_of_work.commit()
                attempts[event.attempt_number] = attempt
                return
            attempt = attempts.get(event.attempt_number)
            if attempt is None:
                raise RuntimeError("Provider Attempt terminal event has no durable start")
            if event.status is ProviderAttemptStatus.SUCCEEDED:
                attempt.succeed(dict(event.usage), usage_cost=event.usage_cost)
            else:
                if event.error_category is None:
                    raise RuntimeError("failed Provider Attempt has no error category")
                attempt.fail(
                    error_category=event.error_category,
                    usage=dict(event.usage),
                    usage_cost=event.usage_cost,
                )
            with self._unit_of_work_factory() as unit_of_work:
                unit_of_work.assistant.update_provider_attempt(attempt)
                unit_of_work.commit()

        return observe


__all__ = ["ProviderAttemptRecorder"]
