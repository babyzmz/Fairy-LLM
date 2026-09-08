from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fairy_core.assistant.models import AssistantTurn, AssistantTurnStatus
from fairy_core.assistant.schedule_models import (
    AssistantOccurrenceStatus,
    AssistantSchedule,
    AssistantScheduleOccurrence,
    AssistantScheduleStatus,
)
from fairy_core.contracts.assistant_schedules import AssistantBackgroundTaskListInput
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.workflow.models import WorkflowRunStatus

_ACTIVE_TURN_STATUSES = frozenset(
    {
        AssistantTurnStatus.CREATED,
        AssistantTurnStatus.RUNNING,
        AssistantTurnStatus.WAITING_FOR_TOOL,
        AssistantTurnStatus.WAITING_FOR_INPUT,
    }
)
_TERMINAL_TURN_STATUSES = frozenset(
    {
        AssistantTurnStatus.COMPLETED,
        AssistantTurnStatus.FAILED,
        AssistantTurnStatus.CANCELLED,
    }
)


class AssistantBackgroundTaskProjection:
    def __init__(self, unit_of_work_factory: CoreUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def list(self, request: AssistantBackgroundTaskListInput) -> dict[str, object]:
        now = datetime.now(UTC)
        recent_since = now - timedelta(hours=24)
        with self._unit_of_work_factory() as unit_of_work:
            active_turns = unit_of_work.assistant.list_turns(
                statuses=_ACTIVE_TURN_STATUSES,
                limit=200,
            )
            recent_turns = unit_of_work.assistant.list_turns(
                statuses=_TERMINAL_TURN_STATUSES,
                updated_since=recent_since,
                limit=100,
            )
            schedules = unit_of_work.assistant_schedules.list(limit=200)
            turns = (*active_turns, *recent_turns)
            conversations = {
                item.id: item
                for item in unit_of_work.state.get_conversations_by_ids(
                    tuple(
                        {turn.conversation_id for turn in turns}
                        | {schedule.conversation_id for schedule in schedules}
                    ),
                )
            }
            tasks = {
                item.id: item
                for item in unit_of_work.state.get_tasks_by_ids(
                    tuple({turn.task_id for turn in turns}),
                )
            }
            occurrences = unit_of_work.assistant_schedules.occurrences_for_projection(
                turn_ids=tuple(turn.id for turn in turns),
                schedule_ids=tuple(schedule.id for schedule in schedules),
            )
            by_turn = {}
            by_schedule = {}
            for occurrence in occurrences:
                if occurrence.turn_id is not None:
                    if occurrence.turn_id in by_turn:
                        raise ValueError("Multiple Schedule occurrences are bound to one Turn")
                    by_turn[occurrence.turn_id] = occurrence
                if occurrence.status in {
                    AssistantOccurrenceStatus.PENDING,
                    AssistantOccurrenceStatus.DISPATCHED,
                }:
                    by_schedule.setdefault(occurrence.schedule_id, occurrence)
            active_items = [
                _turn_item(
                    turn,
                    request.current_conversation_id,
                    conversations.get(turn.conversation_id),
                    tasks.get(turn.task_id),
                    by_turn.get(turn.id),
                )
                for turn in active_turns
            ]
            recent_items = [
                _turn_item(
                    turn,
                    request.current_conversation_id,
                    conversations.get(turn.conversation_id),
                    tasks.get(turn.task_id),
                    by_turn.get(turn.id),
                )
                for turn in recent_turns
            ]
            represented_active_schedules = {
                item["schedule_id"] for item in active_items if item["schedule_id"] is not None
            }
            for schedule in schedules:
                occurrence = by_schedule.get(schedule.id)
                if (
                    occurrence is not None
                    and occurrence.status is AssistantOccurrenceStatus.DISPATCHED
                    and schedule.id in represented_active_schedules
                ):
                    continue
                item = _schedule_item(
                    conversations.get(schedule.conversation_id),
                    schedule,
                    occurrence,
                    request.current_conversation_id,
                )
                if schedule.status in {
                    AssistantScheduleStatus.ACTIVE,
                    AssistantScheduleStatus.PAUSED,
                }:
                    active_items.append(item)
                elif schedule.updated_at >= recent_since and schedule.attention_code is not None:
                    recent_items.append(item)

        current = sorted(
            (item for item in active_items if item["current_conversation"]),
            key=_active_sort_key,
        )
        other = sorted(
            (item for item in active_items if not item["current_conversation"]),
            key=_active_sort_key,
        )
        recent = sorted(
            recent_items,
            key=lambda item: (item["updated_at"], item["id"]),
            reverse=True,
        )[: request.recent_limit]
        return {
            "current": tuple(current),
            "other": tuple(other),
            "recent": tuple(recent),
            "nonterminal_count": len(active_items),
        }


def _turn_item(turn: AssistantTurn, current_conversation_id, conversation, task, occurrence):
    workflow = turn.workflow_summary
    interpretation = turn.interpretation_summary
    workflow_status = workflow.status if workflow is not None else None
    status = _turn_status(turn, workflow_status)
    return {
        "id": f"turn:{turn.id}",
        "kind": "scheduled_turn" if occurrence is not None else "turn",
        "conversation_id": turn.conversation_id,
        "conversation_title": (
            conversation.title if conversation is not None else "Unavailable chat"
        ),
        "project_id": task.project_id if task is not None else None,
        "task_id": turn.task_id,
        "turn_id": turn.id,
        "workflow_run_id": turn.workflow_run_id,
        "schedule_id": occurrence.schedule_id if occurrence is not None else None,
        "occurrence_id": occurrence.id if occurrence is not None else None,
        "title": _title(task.display_title if task is not None else "Fairy task"),
        "status": status,
        "public_error": (
            occurrence.public_error
            if occurrence is not None
            else interpretation.clarification_question
            if turn.status is AssistantTurnStatus.WAITING_FOR_INPUT and interpretation is not None
            else "The task failed."
            if turn.status is AssistantTurnStatus.FAILED
            else None
        ),
        "attention_code": (
            "clarification_required"
            if turn.status is AssistantTurnStatus.WAITING_FOR_INPUT
            else None
        ),
        "current_conversation": turn.conversation_id == current_conversation_id,
        "scheduled_for": occurrence.scheduled_for if occurrence is not None else None,
        "next_fire_at": None,
        "schedule_revision": occurrence.schedule_revision if occurrence is not None else None,
        "turn_status": turn.status,
        "turn_cancellation_revision": turn.cancellation_revision,
        "workflow_budget_tier": workflow.budget_tier if workflow is not None else None,
        "created_at": turn.created_at,
        "updated_at": turn.updated_at,
        "can_pause": workflow_status in {WorkflowRunStatus.QUEUED, WorkflowRunStatus.RUNNING},
        "can_resume": workflow_status is WorkflowRunStatus.PAUSED,
        "can_cancel": turn.status in _ACTIVE_TURN_STATUSES,
        "can_run_now": False,
    }


def _schedule_item(
    conversation,
    schedule: AssistantSchedule,
    occurrence: AssistantScheduleOccurrence | None,
    current_conversation_id,
):
    status = (
        "queued"
        if occurrence is not None and occurrence.status is AssistantOccurrenceStatus.PENDING
        else "attention_required"
        if schedule.attention_code is not None
        else "scheduled"
        if schedule.status is AssistantScheduleStatus.ACTIVE
        else schedule.status.value
    )
    return {
        "id": (
            f"occurrence:{occurrence.id}" if occurrence is not None else f"schedule:{schedule.id}"
        ),
        "kind": "occurrence" if occurrence is not None else "schedule",
        "conversation_id": schedule.conversation_id,
        "conversation_title": (
            conversation.title if conversation is not None else "Unavailable chat"
        ),
        "project_id": schedule.project_id,
        "task_id": None,
        "turn_id": occurrence.turn_id if occurrence is not None else None,
        "workflow_run_id": occurrence.workflow_run_id if occurrence is not None else None,
        "schedule_id": schedule.id,
        "occurrence_id": occurrence.id if occurrence is not None else None,
        "title": _title(schedule.instruction),
        "status": status,
        "public_error": occurrence.public_error if occurrence is not None else None,
        "attention_code": schedule.attention_code,
        "current_conversation": schedule.conversation_id == current_conversation_id,
        "scheduled_for": occurrence.scheduled_for if occurrence is not None else None,
        "next_fire_at": schedule.next_fire_at,
        "schedule_revision": schedule.active_revision,
        "turn_status": None,
        "turn_cancellation_revision": None,
        "workflow_budget_tier": None,
        "created_at": occurrence.created_at if occurrence is not None else schedule.created_at,
        "updated_at": schedule.updated_at,
        "can_pause": schedule.status is AssistantScheduleStatus.ACTIVE,
        "can_resume": schedule.status is AssistantScheduleStatus.PAUSED,
        "can_cancel": schedule.status
        in {
            AssistantScheduleStatus.ACTIVE,
            AssistantScheduleStatus.PAUSED,
        },
        "can_run_now": (
            schedule.status
            in {
                AssistantScheduleStatus.ACTIVE,
                AssistantScheduleStatus.PAUSED,
            }
            and occurrence is None
        ),
    }


def _turn_status(
    turn: AssistantTurn,
    workflow_status: WorkflowRunStatus | None,
) -> str:
    if workflow_status is WorkflowRunStatus.PAUSED:
        return "paused"
    if turn.status is AssistantTurnStatus.WAITING_FOR_TOOL:
        return "waiting_for_approval"
    if turn.status is AssistantTurnStatus.CREATED:
        return "queued"
    return turn.status.value


def _title(value: str) -> str:
    normalized = " ".join(value.split())
    return normalized[:120] or "Fairy task"


def _active_sort_key(item: dict[str, object]) -> tuple[int, datetime, str]:
    rank = {
        "waiting_for_approval": 0,
        "waiting_for_input": 1,
        "attention_required": 2,
        "running": 3,
        "queued": 4,
        "paused": 5,
        "scheduled": 6,
    }.get(str(item["status"]), 6)
    return rank, item["updated_at"], str(item["id"])


__all__ = ["AssistantBackgroundTaskProjection"]
