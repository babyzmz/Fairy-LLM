from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from fairy_core.assistant.schedule_models import (
    AssistantOccurrenceStatus,
    AssistantSchedule,
    AssistantScheduleOccurrence,
    AssistantScheduleStatus,
    AssistantScheduleTriggerKind,
)
from fairy_core.commanding.types import PermissionProfile
from fairy_core.domain.models import (
    Conversation,
    OperationMode,
    Project,
    ProjectResidency,
    Task,
    WorkspaceType,
)
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory


def _factory(path: Path, *, tenant_id: str = "local") -> SqlAlchemyUnitOfWorkFactory:
    return SqlAlchemyUnitOfWorkFactory(
        create_sqlite_core_engine(path, tenant_id=tenant_id),
        tenant_id=tenant_id,
    )


def _seed(factory: SqlAlchemyUnitOfWorkFactory, *, label: str = "schedule") -> Task:
    project = Project.create(name=label, residency=ProjectResidency.LOCAL_ONLY)
    conversation = Conversation.create(
        project_id=project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=None,
    )
    task = Task.create(
        project_id=project.id,
        conversation_id=conversation.id,
        user_request="Seed scheduled work",
        operation_mode=OperationMode.ANSWER,
        base_version_id=None,
        execution_target="local",
    )
    with factory() as unit_of_work:
        unit_of_work.state.save_project(project)
        unit_of_work.state.save_conversation(conversation)
        unit_of_work.state.save_task(task, idempotency_key=f"task:{label}")
        unit_of_work.commit()
    return task


def _schedule(task: Task, *, key: str = "schedule:one") -> AssistantSchedule:
    now = datetime(2026, 8, 7, 1, 0, tzinfo=UTC)
    return AssistantSchedule.create(
        conversation_id=task.conversation_id,
        task_id=task.id,
        project_id=task.project_id,
        workspace_id=task.workspace_id,
        version_id=task.target_version_id,
        instruction="Check the project status",
        trigger_kind=AssistantScheduleTriggerKind.DAILY,
        trigger_rule={"local_time": "09:00"},
        timezone="Australia/Sydney",
        next_fire_at=now + timedelta(hours=1),
        permission_profile=PermissionProfile.STANDARD,
        profile_id="local-default",
        timeline_sequence=1,
        idempotency_key=key,
        now=now,
    )


def test_schedule_model_enforces_state_and_binding_invariants(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "models.db")
    task = _seed(factory)
    schedule = _schedule(task)
    now = schedule.created_at + timedelta(minutes=1)

    paused = schedule.pause(now=now, attention_code="MODEL_UNAVAILABLE")
    assert paused.status is AssistantScheduleStatus.PAUSED
    assert paused.attention_code == "MODEL_UNAVAILABLE"
    resumed = paused.resume(next_fire_at=now + timedelta(hours=1), now=now)
    assert resumed.status is AssistantScheduleStatus.ACTIVE
    assert resumed.attention_code is None
    cancelled = resumed.cancel(now=now)
    assert cancelled.status is AssistantScheduleStatus.CANCELLED
    with pytest.raises(ValueError, match="Only an active"):
        cancelled.pause(now=now)


def test_repository_persists_idempotent_schedules_and_occurrences(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "schedule.db")
    task = _seed(factory)
    schedule = _schedule(task)

    with factory() as unit_of_work:
        persisted = unit_of_work.assistant_schedules.create(schedule)
        replayed = unit_of_work.assistant_schedules.create(schedule)
        occurrence = unit_of_work.assistant_schedules.create_occurrence(
            AssistantScheduleOccurrence.pending(
                schedule_id=schedule.id,
                schedule_revision=1,
                scheduled_for=schedule.next_fire_at,
                coalesced_count=2,
                now=schedule.created_at,
            )
        )
        unit_of_work.commit()

    assert replayed == persisted
    with factory() as unit_of_work:
        loaded = unit_of_work.assistant_schedules.get(schedule.id)
        replacement = AssistantScheduleOccurrence.pending(
            schedule_id=schedule.id,
            schedule_revision=1,
            scheduled_for=schedule.next_fire_at,
        )
        duplicate = unit_of_work.assistant_schedules.create_occurrence(
            replace(occurrence, id=replacement.id)
        )
        occurrences = unit_of_work.assistant_schedules.list_occurrences(schedule_id=schedule.id)
        unit_of_work.commit()

    assert loaded == schedule
    assert duplicate.id == occurrence.id
    assert len(occurrences) == 1
    assert occurrences[0].status is AssistantOccurrenceStatus.PENDING
    assert occurrences[0].coalesced_count == 2


def test_schedule_save_is_revision_fenced_and_tenant_scoped(tmp_path: Path) -> None:
    path = tmp_path / "scope.db"
    engine = create_sqlite_core_engine(path, tenant_id="tenant-a")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    other = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-b")
    task = _seed(factory)
    schedule = _schedule(task)
    with factory() as unit_of_work:
        unit_of_work.assistant_schedules.create(schedule)
        unit_of_work.commit()
    with other() as unit_of_work:
        assert unit_of_work.assistant_schedules.get(schedule.id) is None

    changed = replace(
        schedule,
        instruction="Check the revised project status",
        active_revision=2,
        updated_at=schedule.updated_at + timedelta(seconds=1),
    )
    with factory() as unit_of_work:
        saved = unit_of_work.assistant_schedules.save(changed, expected_revision=1)
        unit_of_work.commit()
    assert saved.active_revision == 2
    with factory() as unit_of_work, pytest.raises(ValueError, match="revision changed"):
        unit_of_work.assistant_schedules.save(changed, expected_revision=1)


def test_occurrence_rejects_partial_turn_binding() -> None:
    pending = AssistantScheduleOccurrence.pending(
        schedule_id=_dummy_uuid(1),
        schedule_revision=1,
        scheduled_for=datetime.now(UTC),
    )
    with pytest.raises(ValueError, match="bindings must be paired"):
        replace(pending, turn_id=_dummy_uuid(2))


def _dummy_uuid(value: int) -> UUID:
    return UUID(int=value)
