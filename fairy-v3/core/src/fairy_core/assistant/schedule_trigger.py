from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event, RLock, Thread
from typing import Protocol
from uuid import UUID

from fairy_core.assistant.models import AssistantTurnStatus
from fairy_core.assistant.schedule_events import append_schedule_change
from fairy_core.assistant.schedule_models import (
    AssistantOccurrenceStatus,
    AssistantSchedule,
    AssistantScheduleClaim,
    AssistantScheduleOccurrence,
    AssistantScheduleStatus,
)
from fairy_core.assistant.schedule_recurrence import advance_due_schedule
from fairy_core.domain.ids import new_id
from fairy_core.persistence.unit_of_work import CoreUnitOfWork, CoreUnitOfWorkFactory

logger = logging.getLogger(__name__)

SCHEDULE_LEASE_DURATION = timedelta(seconds=30)


class AssistantScheduleOccurrenceDispatcher(Protocol):
    def dispatch(
        self,
        *,
        unit_of_work: CoreUnitOfWork,
        schedule: AssistantSchedule,
        occurrence: AssistantScheduleOccurrence,
        now: datetime,
    ) -> AssistantScheduleOccurrence: ...


class AssistantScheduleOccurrenceRunner(Protocol):
    def dispatch(self, *, occurrence_id: UUID, now: datetime) -> bool: ...


class PendingAssistantScheduleDispatcher:
    def dispatch(
        self,
        *,
        unit_of_work: CoreUnitOfWork,
        schedule: AssistantSchedule,
        occurrence: AssistantScheduleOccurrence,
        now: datetime,
    ) -> AssistantScheduleOccurrence:
        del unit_of_work, schedule, now
        return occurrence


class AssistantScheduleAttentionRequired(RuntimeError):
    def __init__(self, code: str, public_error: str) -> None:
        normalized_code = code.strip()
        normalized_error = public_error.strip()
        if not normalized_code or len(normalized_code) > 128:
            raise ValueError("Assistant schedule attention code is invalid")
        if not normalized_error or len(normalized_error) > 500:
            raise ValueError("Assistant schedule public error is invalid")
        super().__init__(normalized_error)
        self.code = normalized_code
        self.public_error = normalized_error


class AssistantScheduleTriggerService:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        dispatcher: AssistantScheduleOccurrenceDispatcher | None = None,
        runner: AssistantScheduleOccurrenceRunner | None = None,
        lease_duration: timedelta = SCHEDULE_LEASE_DURATION,
        poll_interval: float = 1.0,
        claim_limit: int = 8,
        autostart: bool = True,
    ) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError("Assistant schedule lease duration must be positive")
        if poll_interval <= 0:
            raise ValueError("Assistant schedule poll interval must be positive")
        if not 1 <= claim_limit <= 32:
            raise ValueError("Assistant schedule claim limit is invalid")
        self._unit_of_work_factory = unit_of_work_factory
        self._dispatcher = dispatcher or PendingAssistantScheduleDispatcher()
        self._runner = runner
        self._lease_duration = lease_duration
        self._poll_interval = poll_interval
        self._claim_limit = claim_limit
        self._owner_id = f"assistant-schedule:{new_id()}"
        self._wake = Event()
        self._lock = RLock()
        self._closed = False
        self._started = False
        self._thread = Thread(
            target=self._coordinate,
            name="fairy-assistant-schedules",
            daemon=True,
        )
        if autostart:
            self.start()

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Assistant Schedule Trigger Service is closing")
            if self._started:
                return
            self._started = True
            self._thread.start()
        self._wake.set()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._wake.set()
        if self._started:
            self._thread.join()

    def wake(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Assistant Schedule Trigger Service is closing")
        self._wake.set()

    def run_once(self, *, now: datetime | None = None) -> int:
        current = _aware_utc(now or datetime.now(UTC))
        self._reconcile_dispatched(now=current)
        with self._unit_of_work_factory() as unit_of_work:
            claims = unit_of_work.assistant_schedules.claim_ready(
                worker_id=self._owner_id,
                now=current,
                lease_until=current + self._lease_duration,
                limit=self._claim_limit,
            )
            if claims:
                unit_of_work.commit()
        processed = 0
        for claim in claims:
            try:
                settled, pending_id = self._process_claim(claim, now=current)
                if settled:
                    processed += 1
                if pending_id is not None and self._runner is not None:
                    try:
                        self._runner.dispatch(occurrence_id=pending_id, now=current)
                    except AssistantScheduleAttentionRequired as attention:
                        self._record_pending_attention(
                            pending_id,
                            attention=attention,
                            now=current,
                        )
            except Exception:
                logger.exception("Assistant schedule %s trigger failed", claim.schedule_id)
                self._abandon(claim)
        return processed

    def record_outcome(
        self,
        *,
        occurrence_id: UUID,
        status: AssistantOccurrenceStatus,
        public_error: str | None = None,
        attention_code: str | None = None,
        now: datetime | None = None,
    ) -> tuple[AssistantScheduleOccurrence, AssistantSchedule]:
        current = _aware_utc(now or datetime.now(UTC))
        with self._unit_of_work_factory() as unit_of_work:
            occurrence = unit_of_work.assistant_schedules.get_occurrence(occurrence_id)
            if occurrence is None:
                raise KeyError(f"Assistant occurrence not found: {occurrence_id}")
            if occurrence.status is status:
                schedule = unit_of_work.assistant_schedules.get(occurrence.schedule_id)
                if schedule is None:
                    raise KeyError(f"Assistant schedule not found: {occurrence.schedule_id}")
                return occurrence, schedule
            settled = occurrence.settle(
                status=status,
                now=current,
                public_error=public_error,
            )
            result = unit_of_work.assistant_schedules.record_occurrence_outcome(
                settled,
                expected_status=AssistantOccurrenceStatus.DISPATCHED,
                attention_code=attention_code,
            )
            append_schedule_change(unit_of_work, result[1], result[0])
            unit_of_work.commit()
        self._wake.set()
        return result

    def _coordinate(self) -> None:
        while True:
            self._wake.wait(self._poll_interval)
            self._wake.clear()
            with self._lock:
                if self._closed:
                    return
            try:
                self.run_once()
            except Exception:
                logger.exception("Assistant schedule coordinator iteration failed")

    def _process_claim(
        self,
        claim: AssistantScheduleClaim,
        *,
        now: datetime,
    ) -> tuple[bool, UUID | None]:
        with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.assistant_schedules
            schedule = repository.get(claim.schedule_id)
            if (
                schedule is None
                or schedule.lease_owner != claim.lease_owner
                or schedule.lease_fence != claim.lease_fence
                or schedule.active_revision != claim.active_revision
            ):
                return False, None
            pending = repository.get_pending_occurrence(schedule_id=schedule.id)
            original_pending = pending
            advance = (
                advance_due_schedule(schedule, now=now)
                if schedule.status is AssistantScheduleStatus.ACTIVE
                else None
            )
            if advance is not None:
                if pending is None:
                    pending = repository.create_occurrence(
                        AssistantScheduleOccurrence.pending(
                            schedule_id=schedule.id,
                            schedule_revision=schedule.active_revision,
                            scheduled_for=advance.latest_due_at,
                            coalesced_count=advance.missed_count,
                            now=now,
                        )
                    )
                elif advance.latest_due_at > pending.scheduled_for:
                    pending = repository.save_occurrence(
                        pending.coalesce(
                            scheduled_for=advance.latest_due_at,
                            merged_count=advance.missed_count + 1,
                        ),
                        expected_status=AssistantOccurrenceStatus.PENDING,
                    )
                schedule = replace(
                    schedule,
                    next_fire_at=advance.next_fire_at or advance.latest_due_at,
                    last_fire_at=advance.latest_due_at,
                    status=(
                        AssistantScheduleStatus.COMPLETED
                        if advance.next_fire_at is None
                        else schedule.status
                    ),
                    completed_at=now if advance.next_fire_at is None else schedule.completed_at,
                    updated_at=now,
                )
            if pending is not None:
                try:
                    dispatched = self._dispatcher.dispatch(
                        unit_of_work=unit_of_work,
                        schedule=schedule,
                        occurrence=pending,
                        now=now,
                    )
                except AssistantScheduleAttentionRequired as attention:
                    pending = replace(
                        pending,
                        status=AssistantOccurrenceStatus.ATTENTION_REQUIRED,
                        public_error=attention.public_error,
                        completed_at=now,
                    )
                    repository.save_occurrence(
                        pending,
                        expected_status=AssistantOccurrenceStatus.PENDING,
                    )
                    if schedule.status is AssistantScheduleStatus.ACTIVE:
                        schedule = schedule.pause(now=now, attention_code=attention.code)
                    else:
                        schedule = replace(
                            schedule,
                            attention_code=attention.code,
                            updated_at=now,
                        )
                else:
                    if dispatched.id != pending.id or dispatched.schedule_id != schedule.id:
                        raise ValueError("Assistant schedule dispatcher returned different work")
                    if dispatched != pending:
                        pending = repository.save_occurrence(
                            dispatched,
                            expected_status=AssistantOccurrenceStatus.PENDING,
                        )
            schedule = replace(schedule, lease_owner=None, lease_until=None)
            settled = repository.settle_claim(claim, schedule)
            if settled is None:
                return False, None
            if advance is not None or pending != original_pending:
                append_schedule_change(unit_of_work, settled, pending)
            unit_of_work.commit()
            return (
                True,
                pending.id
                if pending is not None and pending.status is AssistantOccurrenceStatus.PENDING
                else None,
            )

    def _reconcile_dispatched(self, *, now: datetime) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            occurrences = unit_of_work.assistant_schedules.list_occurrences_by_status(
                statuses=frozenset({AssistantOccurrenceStatus.DISPATCHED}),
                limit=100,
            )
            changed = False
            for occurrence in occurrences:
                assert occurrence.turn_id is not None
                turn = unit_of_work.assistant.get_turn(occurrence.turn_id)
                if turn is None or turn.status not in {
                    AssistantTurnStatus.COMPLETED,
                    AssistantTurnStatus.FAILED,
                    AssistantTurnStatus.CANCELLED,
                }:
                    continue
                status = {
                    AssistantTurnStatus.COMPLETED: AssistantOccurrenceStatus.SUCCEEDED,
                    AssistantTurnStatus.FAILED: AssistantOccurrenceStatus.FAILED,
                    AssistantTurnStatus.CANCELLED: AssistantOccurrenceStatus.CANCELLED,
                }[turn.status]
                public_error = (
                    "The scheduled run failed."
                    if status is AssistantOccurrenceStatus.FAILED
                    else None
                )
                outcome, schedule = unit_of_work.assistant_schedules.record_occurrence_outcome(
                    occurrence.settle(
                        status=status,
                        now=now,
                        public_error=public_error,
                    ),
                    expected_status=AssistantOccurrenceStatus.DISPATCHED,
                )
                append_schedule_change(unit_of_work, schedule, outcome)
                changed = True
            if changed:
                unit_of_work.commit()

    def _record_pending_attention(
        self,
        occurrence_id: UUID,
        *,
        attention: AssistantScheduleAttentionRequired,
        now: datetime,
    ) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            occurrence = unit_of_work.assistant_schedules.get_occurrence(occurrence_id)
            if occurrence is None or occurrence.status is not AssistantOccurrenceStatus.PENDING:
                return
            changed_occurrence = unit_of_work.assistant_schedules.save_occurrence(
                replace(
                    occurrence,
                    status=AssistantOccurrenceStatus.ATTENTION_REQUIRED,
                    public_error=attention.public_error,
                    completed_at=now,
                ),
                expected_status=AssistantOccurrenceStatus.PENDING,
            )
            schedule = unit_of_work.assistant_schedules.get(occurrence.schedule_id)
            if schedule is not None and schedule.status is AssistantScheduleStatus.ACTIVE:
                schedule = unit_of_work.assistant_schedules.save(
                    schedule.pause(now=now, attention_code=attention.code),
                    expected_revision=schedule.active_revision,
                )
            elif schedule is not None and schedule.attention_code != attention.code:
                # A due one-shot schedule is terminal as soon as its single
                # occurrence is materialized. Preserve the attention reason on
                # that terminal definition so the UI cannot misreport it as a
                # normally completed task.
                schedule = unit_of_work.assistant_schedules.save(
                    replace(schedule, attention_code=attention.code, updated_at=now),
                    expected_revision=schedule.active_revision,
                )
            if schedule is not None:
                append_schedule_change(unit_of_work, schedule, changed_occurrence)
            unit_of_work.commit()

    def _abandon(self, claim: AssistantScheduleClaim) -> None:
        try:
            with self._unit_of_work_factory() as unit_of_work:
                if unit_of_work.assistant_schedules.abandon_claim(claim):
                    unit_of_work.commit()
        except Exception:
            logger.exception("Assistant schedule %s could not be abandoned", claim.schedule_id)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Assistant schedule timestamps must be timezone-aware")
    return value.astimezone(UTC)


__all__ = [
    "SCHEDULE_LEASE_DURATION",
    "AssistantScheduleAttentionRequired",
    "AssistantScheduleOccurrenceDispatcher",
    "AssistantScheduleOccurrenceRunner",
    "AssistantScheduleTriggerService",
    "PendingAssistantScheduleDispatcher",
]
