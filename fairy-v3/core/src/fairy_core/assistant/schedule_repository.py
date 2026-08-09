from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, exists, insert, or_, select, update
from sqlalchemy.engine import Connection

from fairy_core.assistant.interpretation import RequestAction
from fairy_core.assistant.schedule_models import (
    AssistantOccurrenceStatus,
    AssistantSchedule,
    AssistantScheduleClaim,
    AssistantScheduleOccurrence,
    AssistantScheduleStatus,
    AssistantScheduleTriggerKind,
)
from fairy_core.commanding.types import PermissionProfile
from fairy_core.contracts.common import ExecutionTarget
from fairy_core.domain.models import OperationMode
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

    def claim_ready(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_until: datetime,
        limit: int = 1,
    ) -> tuple[AssistantScheduleClaim, ...]:
        normalized_worker = worker_id.strip()
        if not normalized_worker or len(normalized_worker) > 128:
            raise ValueError("Assistant schedule worker id is invalid")
        if now.tzinfo is None or lease_until.tzinfo is None or lease_until <= now:
            raise ValueError("Assistant schedule claim lease is invalid")
        if not 1 <= limit <= 32:
            raise ValueError("Assistant schedule claim limit is invalid")
        pending = exists(
            select(1).where(
                assistant_schedule_occurrences.c.tenant_id == assistant_schedules.c.tenant_id,
                assistant_schedule_occurrences.c.schedule_id == assistant_schedules.c.id,
                assistant_schedule_occurrences.c.status == AssistantOccurrenceStatus.PENDING.value,
            )
        )
        ready = or_(
            and_(
                assistant_schedules.c.status == AssistantScheduleStatus.ACTIVE.value,
                assistant_schedules.c.next_fire_at <= now,
            ),
            pending,
        )
        lease_available = or_(
            assistant_schedules.c.lease_until.is_(None),
            assistant_schedules.c.lease_until <= now,
        )
        statement = (
            select(assistant_schedules)
            .where(
                assistant_schedules.c.tenant_id == self._tenant_id,
                ready,
                lease_available,
            )
            .order_by(
                assistant_schedules.c.next_fire_at,
                assistant_schedules.c.created_at,
                assistant_schedules.c.id,
            )
            .limit(limit)
        )
        if self._connection.dialect.name == "postgresql":
            statement = statement.with_for_update(of=assistant_schedules, skip_locked=True)
        rows = self._connection.execute(statement).mappings().all()
        claims: list[AssistantScheduleClaim] = []
        for row in rows:
            previous_fence = int(row["lease_fence"])
            result = self._connection.execute(
                update(assistant_schedules)
                .where(
                    assistant_schedules.c.tenant_id == self._tenant_id,
                    assistant_schedules.c.id == row["id"],
                    assistant_schedules.c.lease_fence == previous_fence,
                    or_(
                        assistant_schedules.c.lease_until.is_(None),
                        assistant_schedules.c.lease_until <= now,
                    ),
                    ready,
                )
                .values(
                    lease_owner=normalized_worker,
                    lease_until=lease_until,
                    lease_fence=previous_fence + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                continue
            claims.append(
                AssistantScheduleClaim(
                    schedule_id=UUID(str(row["id"])),
                    lease_owner=normalized_worker,
                    lease_fence=previous_fence + 1,
                    active_revision=int(row["active_revision"]),
                )
            )
        return tuple(claims)

    def settle_claim(
        self,
        claim: AssistantScheduleClaim,
        schedule: AssistantSchedule,
    ) -> AssistantSchedule | None:
        if schedule.id != claim.schedule_id:
            raise ValueError("Assistant schedule result does not match its claim")
        if schedule.lease_owner is not None or schedule.lease_until is not None:
            raise ValueError("Settled Assistant schedule must release its lease")
        values = _schedule_record(self._tenant_id, schedule)
        values.pop("tenant_id")
        values.pop("id")
        result = self._connection.execute(
            update(assistant_schedules)
            .where(
                assistant_schedules.c.tenant_id == self._tenant_id,
                assistant_schedules.c.id == str(claim.schedule_id),
                assistant_schedules.c.lease_owner == claim.lease_owner,
                assistant_schedules.c.lease_fence == claim.lease_fence,
                assistant_schedules.c.active_revision == claim.active_revision,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            return None
        return self.get(claim.schedule_id)

    def abandon_claim(self, claim: AssistantScheduleClaim) -> bool:
        result = self._connection.execute(
            update(assistant_schedules)
            .where(
                assistant_schedules.c.tenant_id == self._tenant_id,
                assistant_schedules.c.id == str(claim.schedule_id),
                assistant_schedules.c.lease_owner == claim.lease_owner,
                assistant_schedules.c.lease_fence == claim.lease_fence,
            )
            .values(
                lease_owner=None,
                lease_until=None,
                lease_fence=claim.lease_fence + 1,
            )
        )
        return result.rowcount == 1

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
        if occurrence.idempotency_key is not None:
            replay = self.get_occurrence_by_idempotency_key(
                schedule_id=occurrence.schedule_id,
                idempotency_key=occurrence.idempotency_key,
            )
            if replay is not None:
                return replay
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

    def get_occurrence_for_turn(self, turn_id: UUID) -> AssistantScheduleOccurrence | None:
        row = (
            self._connection.execute(
                select(assistant_schedule_occurrences).where(
                    assistant_schedule_occurrences.c.tenant_id == self._tenant_id,
                    assistant_schedule_occurrences.c.turn_id == str(turn_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        return _occurrence_from_row(row) if row is not None else None

    def get_occurrence_by_idempotency_key(
        self,
        *,
        schedule_id: UUID,
        idempotency_key: str,
    ) -> AssistantScheduleOccurrence | None:
        row = (
            self._connection.execute(
                select(assistant_schedule_occurrences).where(
                    assistant_schedule_occurrences.c.tenant_id == self._tenant_id,
                    assistant_schedule_occurrences.c.schedule_id == str(schedule_id),
                    assistant_schedule_occurrences.c.idempotency_key == idempotency_key,
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

    def get_pending_occurrence(
        self,
        *,
        schedule_id: UUID,
    ) -> AssistantScheduleOccurrence | None:
        return self._get_occurrence_with_status(
            schedule_id=schedule_id,
            statuses=(AssistantOccurrenceStatus.PENDING,),
        )

    def get_active_occurrence(
        self,
        *,
        schedule_id: UUID,
    ) -> AssistantScheduleOccurrence | None:
        return self._get_occurrence_with_status(
            schedule_id=schedule_id,
            statuses=(
                AssistantOccurrenceStatus.PENDING,
                AssistantOccurrenceStatus.DISPATCHED,
            ),
        )

    def _get_occurrence_with_status(
        self,
        *,
        schedule_id: UUID,
        statuses: tuple[AssistantOccurrenceStatus, ...],
    ) -> AssistantScheduleOccurrence | None:
        row = (
            self._connection.execute(
                select(assistant_schedule_occurrences)
                .where(
                    assistant_schedule_occurrences.c.tenant_id == self._tenant_id,
                    assistant_schedule_occurrences.c.schedule_id == str(schedule_id),
                    assistant_schedule_occurrences.c.status.in_(
                        tuple(status.value for status in statuses)
                    ),
                )
                .order_by(
                    assistant_schedule_occurrences.c.scheduled_for.desc(),
                    assistant_schedule_occurrences.c.id.desc(),
                )
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        return _occurrence_from_row(row) if row is not None else None

    def save_occurrence(
        self,
        occurrence: AssistantScheduleOccurrence,
        *,
        expected_status: AssistantOccurrenceStatus,
    ) -> AssistantScheduleOccurrence:
        values = _occurrence_record(self._tenant_id, occurrence)
        values.pop("tenant_id")
        values.pop("id")
        values.pop("schedule_id")
        result = self._connection.execute(
            update(assistant_schedule_occurrences)
            .where(
                assistant_schedule_occurrences.c.tenant_id == self._tenant_id,
                assistant_schedule_occurrences.c.id == str(occurrence.id),
                assistant_schedule_occurrences.c.schedule_id == str(occurrence.schedule_id),
                assistant_schedule_occurrences.c.status == expected_status.value,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise ValueError("Assistant occurrence status changed concurrently")
        persisted = self.get_occurrence(occurrence.id)
        assert persisted is not None
        return persisted

    def record_occurrence_outcome(
        self,
        occurrence: AssistantScheduleOccurrence,
        *,
        expected_status: AssistantOccurrenceStatus,
        attention_code: str | None = None,
    ) -> tuple[AssistantScheduleOccurrence, AssistantSchedule]:
        if occurrence.status not in {
            AssistantOccurrenceStatus.SUCCEEDED,
            AssistantOccurrenceStatus.FAILED,
            AssistantOccurrenceStatus.CANCELLED,
            AssistantOccurrenceStatus.ATTENTION_REQUIRED,
        }:
            raise ValueError("Assistant occurrence outcome is not terminal")
        persisted = self.save_occurrence(occurrence, expected_status=expected_status)
        schedule = self.get(occurrence.schedule_id)
        if schedule is None:
            raise KeyError(f"Assistant schedule not found: {occurrence.schedule_id}")
        now = occurrence.completed_at
        assert now is not None
        failures = schedule.consecutive_failures
        values: dict[str, object] = {"updated_at": now}
        if occurrence.status is AssistantOccurrenceStatus.SUCCEEDED:
            values["consecutive_failures"] = 0
        elif occurrence.status is AssistantOccurrenceStatus.FAILED:
            failures += 1
            values["consecutive_failures"] = failures
            if failures >= 3 and schedule.status is AssistantScheduleStatus.ACTIVE:
                values.update(
                    status=AssistantScheduleStatus.PAUSED.value,
                    paused_at=now,
                    attention_code="CONSECUTIVE_FAILURES",
                )
        elif occurrence.status is AssistantOccurrenceStatus.ATTENTION_REQUIRED:
            code = (attention_code or "CONTEXT_INVALID").strip()
            if not code or len(code) > 128:
                raise ValueError("Assistant schedule attention code is invalid")
            values["attention_code"] = code
            if schedule.status is AssistantScheduleStatus.ACTIVE:
                values.update(
                    status=AssistantScheduleStatus.PAUSED.value,
                    paused_at=now,
                )
        self._connection.execute(
            update(assistant_schedules)
            .where(
                assistant_schedules.c.tenant_id == self._tenant_id,
                assistant_schedules.c.id == str(schedule.id),
            )
            .values(**values)
        )
        updated = self.get(schedule.id)
        assert updated is not None
        return persisted, updated

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

    def list_occurrences_by_status(
        self,
        *,
        statuses: frozenset[AssistantOccurrenceStatus],
        limit: int = 100,
    ) -> tuple[AssistantScheduleOccurrence, ...]:
        if not statuses:
            return ()
        if not 1 <= limit <= 500:
            raise ValueError("Assistant occurrence list limit is invalid")
        rows = (
            self._connection.execute(
                select(assistant_schedule_occurrences)
                .where(
                    assistant_schedule_occurrences.c.tenant_id == self._tenant_id,
                    assistant_schedule_occurrences.c.status.in_(
                        tuple(status.value for status in statuses)
                    ),
                )
                .order_by(
                    assistant_schedule_occurrences.c.scheduled_for,
                    assistant_schedule_occurrences.c.id,
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
        "interpretation_action": (
            schedule.interpretation_action.value
            if schedule.interpretation_action is not None
            else None
        ),
        "interpretation_summary": schedule.interpretation_summary,
        "instruction_sha256": schedule.instruction_sha256,
        "operation_mode": schedule.operation_mode.value,
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
        interpretation_action=(
            RequestAction(str(row["interpretation_action"]))
            if row["interpretation_action"] is not None
            else None
        ),
        interpretation_summary=(
            str(row["interpretation_summary"])
            if row["interpretation_summary"] is not None
            else None
        ),
        instruction_sha256=(
            str(row["instruction_sha256"])
            if row["instruction_sha256"] is not None
            else None
        ),
        operation_mode=OperationMode(str(row["operation_mode"])),
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
        "idempotency_key": occurrence.idempotency_key,
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
        idempotency_key=(
            str(row["idempotency_key"]) if row["idempotency_key"] is not None else None
        ),
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
