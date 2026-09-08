from __future__ import annotations

from fairy_core.assistant.schedule_models import AssistantSchedule, AssistantScheduleOccurrence
from fairy_core.commanding.models import EventVisibility


def append_schedule_change(
    unit_of_work,
    schedule: AssistantSchedule,
    occurrence: AssistantScheduleOccurrence | None = None,
) -> None:
    unit_of_work.commands.append_domain_event(
        event_type="assistant.schedule.changed",
        visibility=EventVisibility.USER,
        actor="assistant.schedule",
        message="Scheduled task state changed.",
        project_id=schedule.project_id,
        conversation_id=schedule.conversation_id,
        task_id=schedule.task_id,
        version_id=schedule.version_id,
        payload={
            "schedule_id": str(schedule.id),
            "revision": schedule.active_revision,
            "status": schedule.status.value,
            "attention_code": schedule.attention_code,
            "occurrence_id": str(occurrence.id) if occurrence is not None else None,
            "occurrence_status": occurrence.status.value if occurrence is not None else None,
            "turn_id": str(occurrence.turn_id)
            if occurrence is not None and occurrence.turn_id is not None
            else None,
        },
    )
