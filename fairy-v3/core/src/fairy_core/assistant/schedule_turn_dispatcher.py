from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from threading import RLock
from uuid import UUID

from fairy_core.application.core import CoreApplication
from fairy_core.assistant.ledger import AssistantLedgerApplication
from fairy_core.assistant.schedule_models import (
    AssistantOccurrenceStatus,
    AssistantSchedule,
)
from fairy_core.assistant.schedule_trigger import AssistantScheduleAttentionRequired
from fairy_core.assistant.turn_scheduler import AssistantTurnScheduler
from fairy_core.contracts.common import ExecutionTarget
from fairy_core.contracts.models import TaskCreate
from fairy_core.domain.errors import VersionConflictError
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory

ScheduleModelResolver = Callable[[AssistantSchedule], str]


class AssistantScheduledTurnDispatcher:
    """Idempotently turn one pending Occurrence into normal Assistant work."""

    def __init__(
        self,
        *,
        application: CoreApplication,
        ledger: AssistantLedgerApplication,
        scheduler: AssistantTurnScheduler,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        model_resolver: ScheduleModelResolver | None = None,
    ) -> None:
        self._application = application
        self._ledger = ledger
        self._scheduler = scheduler
        self._unit_of_work_factory = unit_of_work_factory
        self._model_resolver = model_resolver
        self._lock = RLock()

    def dispatch(self, *, occurrence_id: UUID, now: datetime) -> bool:
        del now
        with self._lock:
            return self._dispatch_locked(occurrence_id)

    def _dispatch_locked(self, occurrence_id: UUID) -> bool:
        with self._unit_of_work_factory() as unit_of_work:
            occurrence = unit_of_work.assistant_schedules.get_occurrence(occurrence_id)
            if occurrence is None or occurrence.status is not AssistantOccurrenceStatus.PENDING:
                return False
            schedule = unit_of_work.assistant_schedules.get(occurrence.schedule_id)
            if schedule is None:
                raise AssistantScheduleAttentionRequired(
                    "SCHEDULE_MISSING",
                    "The scheduled task definition is no longer available.",
                )
            profile_id = self._validate_binding(unit_of_work, schedule)
            turn_key = _turn_key(schedule, occurrence.scheduled_for)
            existing = unit_of_work.assistant.find_turn_by_idempotency_key(turn_key)
            active = unit_of_work.assistant.nonterminal_turn_for_conversation(
                schedule.conversation_id
            )
            if active is not None and (existing is None or active.id != existing.id):
                return False

        if existing is None:
            task_key = _task_key(schedule, occurrence.scheduled_for)
            try:
                context = self._application.create_task(
                    TaskCreate(
                        conversation_id=schedule.conversation_id,
                        user_request=schedule.instruction,
                        operation_mode=schedule.operation_mode,
                        execution_target=ExecutionTarget.LOCAL,
                        idempotency_key=task_key,
                    ),
                    base_version_id_override=schedule.version_id,
                )
                turn = self._ledger.create_turn(
                    task_id=context.task.id,
                    profile_id=profile_id,
                    idempotency_key=turn_key,
                    model_selection=schedule.model_selection,
                )
            except VersionConflictError as error:
                raise AssistantScheduleAttentionRequired(
                    "MODEL_OR_VERSION_CHANGED",
                    "The scheduled model or Workspace version changed and needs confirmation.",
                ) from error
        else:
            turn = existing

        if turn.workflow_run_id is None:
            raise AssistantScheduleAttentionRequired(
                "WORKFLOW_BINDING_MISSING",
                "The scheduled Assistant workflow binding is unavailable.",
            )
        with self._unit_of_work_factory() as unit_of_work:
            occurrence = unit_of_work.assistant_schedules.get_occurrence(occurrence_id)
            if occurrence is None or occurrence.status is not AssistantOccurrenceStatus.PENDING:
                return False
            active = unit_of_work.assistant.nonterminal_turn_for_conversation(turn.conversation_id)
            if active is not None and active.id != turn.id:
                return False
            unit_of_work.assistant_schedules.save_occurrence(
                occurrence.dispatch(
                    turn_id=turn.id,
                    workflow_run_id=turn.workflow_run_id,
                    now=turn.created_at,
                ),
                expected_status=AssistantOccurrenceStatus.PENDING,
            )
            unit_of_work.commit()
        self._scheduler.start(turn.id)
        return True

    def _validate_binding(self, unit_of_work, schedule: AssistantSchedule) -> str:
        conversation = unit_of_work.state.get_conversation(schedule.conversation_id)
        if conversation is None:
            raise AssistantScheduleAttentionRequired(
                "CONVERSATION_MISSING",
                "The scheduled conversation is no longer available.",
            )
        if (
            conversation.workspace_id != schedule.workspace_id
            or conversation.project_id != schedule.project_id
        ):
            raise AssistantScheduleAttentionRequired(
                "WORKSPACE_BINDING_CHANGED",
                "The scheduled Workspace binding changed and needs confirmation.",
            )
        if schedule.version_id is not None:
            version = unit_of_work.state.get_version(schedule.version_id)
            if (
                version is None
                or version.workspace_id != schedule.workspace_id
                or version.project_id != schedule.project_id
            ):
                raise AssistantScheduleAttentionRequired(
                    "VERSION_INVALID",
                    "The scheduled Workspace version is no longer available.",
                )
        settings = unit_of_work.execution_settings.get()
        if settings.profile is not schedule.permission_profile:
            raise AssistantScheduleAttentionRequired(
                "PERMISSION_PROFILE_CHANGED",
                "The permission profile changed and needs confirmation before this task can run.",
            )
        if schedule.model_selection is not None:
            current = unit_of_work.model_catalog.get_selection()
            if (
                current.mode is not schedule.model_selection.mode
                or current.model_id != schedule.model_selection.model_id
                or current.allow_free_fallback != schedule.model_selection.allow_free_fallback
                or current.zero_data_retention != schedule.model_selection.zero_data_retention
                or current.revision != schedule.model_selection.revision
            ):
                raise AssistantScheduleAttentionRequired(
                    "MODEL_SELECTION_CHANGED",
                    "The scheduled model selection changed and needs confirmation.",
                )
        if self._model_resolver is not None:
            return self._model_resolver(schedule)
        if schedule.profile_id is None:
            raise AssistantScheduleAttentionRequired(
                "MODEL_RESOLVER_UNAVAILABLE",
                "The scheduled model cannot be resolved on this device.",
            )
        return schedule.profile_id


def _task_key(schedule: AssistantSchedule, scheduled_for: datetime) -> str:
    return f"schedule-task:{schedule.id}:{scheduled_for.isoformat()}"


def _turn_key(schedule: AssistantSchedule, scheduled_for: datetime) -> str:
    return f"schedule-turn:{schedule.id}:{scheduled_for.isoformat()}"


__all__ = ["AssistantScheduledTurnDispatcher", "ScheduleModelResolver"]
