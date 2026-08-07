from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from fairy_core.assistant.schedule_models import (
    AssistantSchedule,
    AssistantScheduleOccurrence,
    AssistantScheduleStatus,
)


class AssistantScheduleRepository(Protocol):
    def create(self, schedule: AssistantSchedule) -> AssistantSchedule: ...

    def get(self, schedule_id: UUID) -> AssistantSchedule | None: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> AssistantSchedule | None: ...

    def save(self, schedule: AssistantSchedule, *, expected_revision: int) -> AssistantSchedule: ...

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

    def get_occurrence_at(
        self,
        *,
        schedule_id: UUID,
        scheduled_for: datetime,
    ) -> AssistantScheduleOccurrence | None: ...

    def list_occurrences(
        self,
        *,
        schedule_id: UUID,
        limit: int = 100,
    ) -> tuple[AssistantScheduleOccurrence, ...]: ...


__all__ = ["AssistantScheduleRepository"]
