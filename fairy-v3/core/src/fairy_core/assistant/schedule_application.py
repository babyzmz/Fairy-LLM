from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from fairy_core.assistant.schedule_events import append_schedule_change
from fairy_core.assistant.schedule_interpretation import interpret_scheduled_instruction
from fairy_core.assistant.schedule_models import (
    AssistantSchedule,
    AssistantScheduleOccurrence,
    AssistantScheduleStatus,
)
from fairy_core.assistant.schedule_recurrence import validate_schedule_rule
from fairy_core.assistant.schedule_trigger import AssistantScheduleTriggerService
from fairy_core.contracts.assistant_schedules import (
    AssistantScheduleCreateInput,
    AssistantScheduleListInput,
    AssistantScheduleRunNowInput,
    AssistantScheduleUpdateInput,
)
from fairy_core.model_catalog.models import ModelSelectionSnapshot
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory


class AssistantScheduleApplication:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        trigger: AssistantScheduleTriggerService,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._trigger = trigger

    def create(
        self,
        request: AssistantScheduleCreateInput,
        *,
        profile_id: str,
        model_selection: ModelSelectionSnapshot | None,
    ) -> AssistantSchedule:
        now = datetime.now(UTC)
        interpretation = interpret_scheduled_instruction(request.instruction)
        with self._unit_of_work_factory() as unit_of_work:
            conversation = unit_of_work.state.get_conversation(request.conversation_id)
            if conversation is None or conversation.workspace_id is None:
                raise KeyError(f"conversation not found: {request.conversation_id}")
            version_id = conversation.active_draft_version_id or conversation.base_version_id
            schedule = AssistantSchedule.create(
                conversation_id=conversation.id,
                project_id=conversation.project_id,
                workspace_id=conversation.workspace_id,
                version_id=version_id,
                instruction=request.instruction,
                operation_mode=request.operation_mode,
                trigger_kind=request.trigger_kind,
                trigger_rule=request.trigger_rule,
                timezone=request.timezone,
                next_fire_at=_aware_utc(request.next_fire_at),
                permission_profile=unit_of_work.execution_settings.get().profile,
                profile_id=profile_id if model_selection is None else None,
                model_selection=model_selection,
                timeline_sequence=unit_of_work.assistant.next_message_sequence(conversation.id),
                idempotency_key=request.idempotency_key,
                interpretation_action=interpretation.action,
                interpretation_summary=interpretation.public_summary,
                instruction_sha256=interpretation.instruction_sha256,
                now=now,
            )
            validate_schedule_rule(schedule)
            persisted = unit_of_work.assistant_schedules.create(schedule)
            if persisted.id == schedule.id:
                append_schedule_change(unit_of_work, persisted)
            unit_of_work.commit()
        self._trigger.wake()
        return persisted

    def get(self, schedule_id: UUID) -> AssistantSchedule:
        with self._unit_of_work_factory() as unit_of_work:
            schedule = unit_of_work.assistant_schedules.get(schedule_id)
        if schedule is None:
            raise KeyError(f"Assistant schedule not found: {schedule_id}")
        return schedule

    def list(self, request: AssistantScheduleListInput) -> dict[str, object]:
        with self._unit_of_work_factory() as unit_of_work:
            schedules = unit_of_work.assistant_schedules.list(
                conversation_id=request.conversation_id,
                statuses=request.statuses,
                limit=request.limit,
            )
        return {"items": schedules}

    def update(self, request: AssistantScheduleUpdateInput) -> AssistantSchedule:
        now = datetime.now(UTC)
        with self._unit_of_work_factory() as unit_of_work:
            current = _require_schedule(unit_of_work, request.schedule_id)
            if current.active_revision != request.expected_revision:
                raise ValueError("Assistant schedule revision changed concurrently")
            if current.status in {
                AssistantScheduleStatus.COMPLETED,
                AssistantScheduleStatus.CANCELLED,
            }:
                raise ValueError("Terminal Assistant schedule cannot be edited")
            interpretation = interpret_scheduled_instruction(request.instruction)
            changed = replace(
                current,
                instruction=request.instruction,
                operation_mode=request.operation_mode,
                trigger_kind=request.trigger_kind,
                trigger_rule=request.trigger_rule,
                timezone=request.timezone,
                next_fire_at=_aware_utc(request.next_fire_at),
                active_revision=current.active_revision + 1,
                attention_code=None,
                updated_at=now,
                interpretation_action=interpretation.action,
                interpretation_summary=interpretation.public_summary,
                instruction_sha256=interpretation.instruction_sha256,
            )
            validate_schedule_rule(changed)
            persisted = unit_of_work.assistant_schedules.save(
                changed,
                expected_revision=request.expected_revision,
            )
            append_schedule_change(unit_of_work, persisted)
            unit_of_work.commit()
        self._trigger.wake()
        return persisted

    def pause(self, schedule_id: UUID, *, expected_revision: int) -> AssistantSchedule:
        with self._unit_of_work_factory() as unit_of_work:
            schedule = _require_revision(unit_of_work, schedule_id, expected_revision)
            persisted = unit_of_work.assistant_schedules.save(
                schedule.pause(now=datetime.now(UTC)),
                expected_revision=expected_revision,
            )
            append_schedule_change(unit_of_work, persisted)
            unit_of_work.commit()
        return persisted

    def resume(self, schedule_id: UUID, *, expected_revision: int) -> AssistantSchedule:
        with self._unit_of_work_factory() as unit_of_work:
            schedule = _require_revision(unit_of_work, schedule_id, expected_revision)
            persisted = unit_of_work.assistant_schedules.save(
                schedule.resume(next_fire_at=schedule.next_fire_at, now=datetime.now(UTC)),
                expected_revision=expected_revision,
            )
            append_schedule_change(unit_of_work, persisted)
            unit_of_work.commit()
        self._trigger.wake()
        return persisted

    def cancel(self, schedule_id: UUID, *, expected_revision: int) -> AssistantSchedule:
        with self._unit_of_work_factory() as unit_of_work:
            schedule = _require_revision(unit_of_work, schedule_id, expected_revision)
            persisted = unit_of_work.assistant_schedules.save(
                schedule.cancel(now=datetime.now(UTC)),
                expected_revision=expected_revision,
            )
            append_schedule_change(unit_of_work, persisted)
            unit_of_work.commit()
        return persisted

    def run_now(self, request: AssistantScheduleRunNowInput) -> AssistantScheduleOccurrence:
        now = datetime.now(UTC)
        with self._unit_of_work_factory() as unit_of_work:
            schedule = _require_revision(
                unit_of_work,
                request.schedule_id,
                request.expected_revision,
            )
            replay = unit_of_work.assistant_schedules.get_occurrence_by_idempotency_key(
                schedule_id=schedule.id,
                idempotency_key=request.idempotency_key,
            )
            if replay is not None:
                return replay
            if (
                unit_of_work.assistant_schedules.get_active_occurrence(schedule_id=schedule.id)
                is not None
            ):
                raise ValueError("Assistant schedule already has an active Occurrence")
            occurrence = unit_of_work.assistant_schedules.create_occurrence(
                AssistantScheduleOccurrence.pending(
                    schedule_id=schedule.id,
                    schedule_revision=schedule.active_revision,
                    scheduled_for=now,
                    idempotency_key=request.idempotency_key,
                    now=now,
                )
            )
            append_schedule_change(unit_of_work, schedule, occurrence)
            unit_of_work.commit()
        self._trigger.wake()
        return occurrence


def _require_schedule(unit_of_work, schedule_id: UUID) -> AssistantSchedule:
    schedule = unit_of_work.assistant_schedules.get(schedule_id)
    if schedule is None:
        raise KeyError(f"Assistant schedule not found: {schedule_id}")
    return schedule


def _require_revision(
    unit_of_work,
    schedule_id: UUID,
    expected_revision: int,
) -> AssistantSchedule:
    schedule = _require_schedule(unit_of_work, schedule_id)
    if schedule.active_revision != expected_revision:
        raise ValueError("Assistant schedule revision changed concurrently")
    return schedule


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Assistant schedule timestamps must be timezone-aware")
    return value.astimezone(UTC)


__all__ = ["AssistantScheduleApplication"]
