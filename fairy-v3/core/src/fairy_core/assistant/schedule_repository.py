from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection

from fairy_core.assistant.schedule_models import (
    AssistantOccurrenceStatus,
    AssistantSchedule,
    AssistantScheduleOccurrence,
    AssistantScheduleStatus,
    AssistantScheduleTriggerKind,
)
from fairy_core.commanding.types import PermissionProfile
from fairy_core.contracts.common import ExecutionTarget
from fairy_core.model_catalog.models import ModelSelectionMode, ModelSelectionSnapshot
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.storage.schema import assistant_schedule_occurrences, assistant_schedules


class SqlAlchemyAssistantScheduleRepository:
    def __init__(self, connection: Connection, *, tenant_id: str) -> None:
        self._connection = connection
        self._tenant_id = normalize_tenant_id(tenant_id)

    def create(self, schedule: AssistantSchedule) -> AssistantSchedule:
        existing = self.get_by_idempotency_key(schedule.idempotency_key)
        if existing is not None:
            if (
                existing.conversation_id != schedule.conversation_id
                or existing.instruction != schedule.instruction
                or existing.trigger_kind is not schedule.trigger_kind
            ):
                raise ValueError("Assistant schedule idempotency key is bound to different work")
            return existing
        self._connection.execute(
            insert(assistant_schedules).values(_schedule_record(self._tenant_id, schedule))
        )
        persisted = self.get(schedule.id)
        assert persisted is not None
        return persisted

    def get(self, schedule_id: UUID) -> AssistantSchedule | None:
        row = (
            self._connection.execute(
                select(assistant_schedules).where(
                    assistant_schedules.c.tenant_id == self._tenant_id,
                    assistant_schedules.c.id == str(schedule_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        return _schedule_from_row(row) if row is not None else None

    def get_by_idempotency_key(self, idempotency_key: str) -> AssistantSchedule | None:
        row = (
            self._connection.execute(
                select(assistant_schedules).where(
                    assistant_schedules.c.tenant_id == self._tenant_id,
                    assistant_schedules.c.idempotency_key == idempotency_key,
                )
            )
            .mappings()
            .one_or_none()
        )
        return _schedule_from_row(row) if row is not None else None

    def save(self, schedule: AssistantSchedule, *, expected_revision: int) -> AssistantSchedule:
        values = _schedule_record(self._tenant_id, schedule)
        values.pop("tenant_id")
        values.pop("id")
        result = self._connection.execute(
            update(assistant_schedules)
            .where(
                assistant_schedules.c.tenant_id == self._tenant_id,
                assistant_schedules.c.id == str(schedule.id),
                assistant_schedules.c.active_revision == expected_revision,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise ValueError("Assistant schedule revision changed concurrently")
        persisted = self.get(schedule.id)
        assert persisted is not None
        return persisted

    def list(
        self,
        *,
        conversation_id: UUID | None = None,
        statuses: frozenset[AssistantScheduleStatus] | None = None,
        limit: int = 100,
    ) -> tuple[AssistantSchedule, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("Assistant schedule list limit is invalid")
        statement = select(assistant_schedules).where(
            assistant_schedules.c.tenant_id == self._tenant_id
        )
        if conversation_id is not None:
            statement = statement.where(
                assistant_schedules.c.conversation_id == str(conversation_id)
            )
        if statuses is not None:
            if not statuses:
                return ()
            statement = statement.where(
                assistant_schedules.c.status.in_(tuple(value.value for value in statuses))
            )
        rows = (
            self._connection.execute(
                statement.order_by(
                    assistant_schedules.c.updated_at.desc(),
                    assistant_schedules.c.id.desc(),
                ).limit(limit)
            )
            .mappings()
            .all()
        )
        return tuple(_schedule_from_row(row) for row in rows)

    def create_occurrence(
        self,
        occurrence: AssistantScheduleOccurrence,
    ) -> AssistantScheduleOccurrence:
        existing = self.get_occurrence_at(
            schedule_id=occurrence.schedule_id,
            scheduled_for=occurrence.scheduled_for,
        )
        if existing is not None:
            return existing
        self._connection.execute(
            insert(assistant_schedule_occurrences).values(
                _occurrence_record(self._tenant_id, occurrence)
            )
        )
        persisted = self.get_occurrence(occurrence.id)
        assert persisted is not None
        return persisted

    def get_occurrence(self, occurrence_id: UUID) -> AssistantScheduleOccurrence | None:
        row = (
            self._connection.execute(
                select(assistant_schedule_occurrences).where(
                    assistant_schedule_occurrences.c.tenant_id == self._tenant_id,
                    assistant_schedule_occurrences.c.id == str(occurrence_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        return _occurrence_from_row(row) if row is not None else None

    def get_occurrence_at(
        self,
        *,
        schedule_id: UUID,
        scheduled_for: datetime,
    ) -> AssistantScheduleOccurrence | None:
        row = (
            self._connection.execute(
                select(assistant_schedule_occurrences).where(
                    assistant_schedule_occurrences.c.tenant_id == self._tenant_id,
                    assistant_schedule_occurrences.c.schedule_id == str(schedule_id),
                    assistant_schedule_occurrences.c.scheduled_for == scheduled_for,
                )
            )
            .mappings()
            .one_or_none()
        )
        return _occurrence_from_row(row) if row is not None else None

    def list_occurrences(
        self,
        *,
        schedule_id: UUID,
        limit: int = 100,
    ) -> tuple[AssistantScheduleOccurrence, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("Assistant occurrence list limit is invalid")
        rows = (
            self._connection.execute(
                select(assistant_schedule_occurrences)
                .where(
                    assistant_schedule_occurrences.c.tenant_id == self._tenant_id,
                    assistant_schedule_occurrences.c.schedule_id == str(schedule_id),
                )
                .order_by(
                    assistant_schedule_occurrences.c.scheduled_for.desc(),
                    assistant_schedule_occurrences.c.id.desc(),
                )
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return tuple(_occurrence_from_row(row) for row in rows)


def _schedule_record(tenant_id: str, schedule: AssistantSchedule) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "id": str(schedule.id),
        "conversation_id": str(schedule.conversation_id),
        "task_id": str(schedule.task_id) if schedule.task_id else None,
        "project_id": str(schedule.project_id) if schedule.project_id else None,
        "workspace_id": str(schedule.workspace_id),
        "version_id": str(schedule.version_id) if schedule.version_id else None,
        "instruction": schedule.instruction,
        "trigger_kind": schedule.trigger_kind.value,
        "trigger_rule": dict(schedule.trigger_rule),
        "timezone": schedule.timezone,
        "next_fire_at": schedule.next_fire_at,
        "execution_target": schedule.execution_target.value,
        "profile_id": schedule.profile_id,
        "model_selection": _selection_record(schedule.model_selection),
        "permission_profile": schedule.permission_profile.value,
        "timeline_sequence": schedule.timeline_sequence,
        "status": schedule.status.value,
        "active_revision": schedule.active_revision,
        "consecutive_failures": schedule.consecutive_failures,
        "idempotency_key": schedule.idempotency_key,
        "lease_owner": schedule.lease_owner,
        "lease_fence": schedule.lease_fence,
        "lease_until": schedule.lease_until,
        "attention_code": schedule.attention_code,
        "created_at": schedule.created_at,
        "updated_at": schedule.updated_at,
        "last_fire_at": schedule.last_fire_at,
        "paused_at": schedule.paused_at,
        "completed_at": schedule.completed_at,
        "cancelled_at": schedule.cancelled_at,
    }


def _schedule_from_row(row: Mapping[str, Any]) -> AssistantSchedule:
    return AssistantSchedule(
        id=UUID(str(row["id"])),
        conversation_id=UUID(str(row["conversation_id"])),
        task_id=_uuid(row["task_id"]),
        project_id=_uuid(row["project_id"]),
        workspace_id=UUID(str(row["workspace_id"])),
        version_id=_uuid(row["version_id"]),
        instruction=str(row["instruction"]),
        trigger_kind=AssistantScheduleTriggerKind(str(row["trigger_kind"])),
        trigger_rule=dict(row["trigger_rule"]),
        timezone=str(row["timezone"]),
        next_fire_at=_datetime(row["next_fire_at"]),
        execution_target=ExecutionTarget(str(row["execution_target"])),
        profile_id=str(row["profile_id"]) if row["profile_id"] is not None else None,
        model_selection=_selection_from_record(row["model_selection"]),
        permission_profile=PermissionProfile(str(row["permission_profile"])),
        timeline_sequence=int(row["timeline_sequence"]),
        status=AssistantScheduleStatus(str(row["status"])),
        active_revision=int(row["active_revision"]),
        consecutive_failures=int(row["consecutive_failures"]),
        idempotency_key=str(row["idempotency_key"]),
        lease_owner=str(row["lease_owner"]) if row["lease_owner"] is not None else None,
        lease_fence=int(row["lease_fence"]),
        lease_until=_optional_datetime(row["lease_until"]),
        attention_code=(str(row["attention_code"]) if row["attention_code"] is not None else None),
        created_at=_datetime(row["created_at"]),
        updated_at=_datetime(row["updated_at"]),
        last_fire_at=_optional_datetime(row["last_fire_at"]),
        paused_at=_optional_datetime(row["paused_at"]),
        completed_at=_optional_datetime(row["completed_at"]),
        cancelled_at=_optional_datetime(row["cancelled_at"]),
    )


def _occurrence_record(
    tenant_id: str,
    occurrence: AssistantScheduleOccurrence,
) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "id": str(occurrence.id),
        "schedule_id": str(occurrence.schedule_id),
        "schedule_revision": occurrence.schedule_revision,
        "scheduled_for": occurrence.scheduled_for,
        "status": occurrence.status.value,
        "coalesced_count": occurrence.coalesced_count,
        "turn_id": str(occurrence.turn_id) if occurrence.turn_id else None,
        "workflow_run_id": (
            str(occurrence.workflow_run_id) if occurrence.workflow_run_id else None
        ),
        "public_error": occurrence.public_error,
        "created_at": occurrence.created_at,
        "dispatched_at": occurrence.dispatched_at,
        "completed_at": occurrence.completed_at,
    }


def _occurrence_from_row(row: Mapping[str, Any]) -> AssistantScheduleOccurrence:
    return AssistantScheduleOccurrence(
        id=UUID(str(row["id"])),
        schedule_id=UUID(str(row["schedule_id"])),
        schedule_revision=int(row["schedule_revision"]),
        scheduled_for=_datetime(row["scheduled_for"]),
        status=AssistantOccurrenceStatus(str(row["status"])),
        coalesced_count=int(row["coalesced_count"]),
        turn_id=_uuid(row["turn_id"]),
        workflow_run_id=_uuid(row["workflow_run_id"]),
        public_error=str(row["public_error"]) if row["public_error"] is not None else None,
        created_at=_datetime(row["created_at"]),
        dispatched_at=_optional_datetime(row["dispatched_at"]),
        completed_at=_optional_datetime(row["completed_at"]),
    )


def _selection_record(selection: ModelSelectionSnapshot | None) -> dict[str, object] | None:
    if selection is None:
        return None
    return {
        "mode": selection.mode.value,
        "model_id": selection.model_id,
        "allow_free_fallback": selection.allow_free_fallback,
        "zero_data_retention": selection.zero_data_retention,
        "revision": selection.revision,
        "captured_at": selection.captured_at.isoformat(),
    }


def _selection_from_record(record: object) -> ModelSelectionSnapshot | None:
    if record is None:
        return None
    if not isinstance(record, dict):
        raise ValueError("Stored Assistant schedule model selection is invalid")
    return ModelSelectionSnapshot(
        mode=ModelSelectionMode(str(record["mode"])),
        model_id=str(record["model_id"]) if record.get("model_id") is not None else None,
        allow_free_fallback=bool(record["allow_free_fallback"]),
        zero_data_retention=bool(record["zero_data_retention"]),
        revision=int(record["revision"]),
        captured_at=_datetime(record["captured_at"]),
    )


def _uuid(value: object) -> UUID | None:
    return UUID(str(value)) if value is not None else None


def _datetime(value: object) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _optional_datetime(value: object) -> datetime | None:
    return _datetime(value) if value is not None else None


__all__ = ["SqlAlchemyAssistantScheduleRepository"]
