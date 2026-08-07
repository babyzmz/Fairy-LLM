from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from fairy_core.assistant.schedule_models import (
    AssistantOccurrenceStatus,
    AssistantSchedule,
    AssistantScheduleClaim,
    AssistantScheduleOccurrence,
    AssistantScheduleStatus,
)


class AssistantScheduleRepository(Protocol):
    def create(self, schedule: AssistantSchedule) -> AssistantSchedule: ...

    def get(self, schedule_id: UUID) -> AssistantSchedule | None: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> AssistantSchedule | None: ...

    def save(self, schedule: AssistantSchedule, *, expected_revision: int) -> AssistantSchedule: ...

    def claim_ready(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_until: datetime,
        limit: int = 1,
    ) -> tuple[AssistantScheduleClaim, ...]: ...

    def settle_claim(
        self,
        claim: AssistantScheduleClaim,
        schedule: AssistantSchedule,
    ) -> AssistantSchedule | None: ...

    def abandon_claim(self, claim: AssistantScheduleClaim) -> bool: ...

    def list(
        self,
        *,
        conversation_id: UUID | None = None,
        statuses: frozenset[AssistantScheduleStatus] | None = None,
        limit: int = 100,
    ) -> tuple[AssistantSchedule, ...]: ...

    def create_occurrence(
        self,
        occurrence: AssistantScheduleOccurrence,
    ) -> AssistantScheduleOccurrence: ...

    def get_occurrence(self, occurrence_id: UUID) -> AssistantScheduleOccurrence | None: ...

    def get_occurrence_for_turn(self, turn_id: UUID) -> AssistantScheduleOccurrence | None: ...

    def get_occurrence_by_idempotency_key(
        self,
        *,
        schedule_id: UUID,
        idempotency_key: str,
    ) -> AssistantScheduleOccurrence | None: ...

    def get_occurrence_at(
        self,
        *,
        schedule_id: UUID,
        scheduled_for: datetime,
    ) -> AssistantScheduleOccurrence | None: ...

    def get_pending_occurrence(
        self,
        *,
        schedule_id: UUID,
    ) -> AssistantScheduleOccurrence | None: ...

    def get_active_occurrence(
        self,
        *,
        schedule_id: UUID,
    ) -> AssistantScheduleOccurrence | None: ...

    def save_occurrence(
        self,
        occurrence: AssistantScheduleOccurrence,
        *,
        expected_status: AssistantOccurrenceStatus,
    ) -> AssistantScheduleOccurrence: ...

    def record_occurrence_outcome(
        self,
        occurrence: AssistantScheduleOccurrence,
        *,
        expected_status: AssistantOccurrenceStatus,
        attention_code: str | None = None,
    ) -> tuple[AssistantScheduleOccurrence, AssistantSchedule]: ...

    def list_occurrences(
        self,
        *,
        schedule_id: UUID,
        limit: int = 100,
    ) -> tuple[AssistantScheduleOccurrence, ...]: ...

    def list_occurrences_by_status(
        self,
        *,
        statuses: frozenset[AssistantOccurrenceStatus],
        limit: int = 100,
    ) -> tuple[AssistantScheduleOccurrence, ...]: ...


__all__ = ["AssistantScheduleRepository"]
