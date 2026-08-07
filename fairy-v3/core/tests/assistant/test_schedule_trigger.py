from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from fairy_core.assistant.models import AssistantTurn
from fairy_core.assistant.schedule_models import (
    AssistantOccurrenceStatus,
    AssistantSchedule,
    AssistantScheduleOccurrence,
    AssistantScheduleStatus,
    AssistantScheduleTriggerKind,
)
from fairy_core.assistant.schedule_recurrence import resolve_first_local_instant
from fairy_core.assistant.schedule_trigger import (
    AssistantScheduleAttentionRequired,
    AssistantScheduleTriggerService,
)
from fairy_core.commanding.types import PermissionProfile
from fairy_core.contracts.common import ExecutionTarget
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import (
    Conversation,
    OperationMode,
    Project,
    ProjectResidency,
    Task,
    WorkspaceType,
)
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import (
    CoreUnitOfWork,
    SqlAlchemyUnitOfWorkFactory,
)
from fairy_core.workflow.models import WorkflowNode, WorkflowRun, WorkflowTriggerKind


class _AttentionDispatcher:
    def dispatch(
        self,
        *,
        unit_of_work: CoreUnitOfWork,
        schedule: AssistantSchedule,
        occurrence: AssistantScheduleOccurrence,
        now: datetime,
    ) -> AssistantScheduleOccurrence:
        del unit_of_work, schedule, occurrence, now
        raise AssistantScheduleAttentionRequired(
            "VERSION_INVALID",
            "The bound Workspace version is no longer available.",
        )


def _factory(path: Path) -> SqlAlchemyUnitOfWorkFactory:
    return SqlAlchemyUnitOfWorkFactory(
        create_sqlite_core_engine(path, tenant_id="local"),
        tenant_id="local",
    )


def _seed(factory: SqlAlchemyUnitOfWorkFactory) -> Task:
    project = Project.create(name="scheduled", residency=ProjectResidency.LOCAL_ONLY)
    conversation = Conversation.create(
        project_id=project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=None,
    )
    task = Task.create(
        project_id=project.id,
        conversation_id=conversation.id,
        user_request="Seed schedule",
        operation_mode=OperationMode.ANSWER,
        base_version_id=None,
        execution_target="local",
    )
    with factory() as unit_of_work:
        unit_of_work.state.save_project(project)
        unit_of_work.state.save_conversation(conversation)
        unit_of_work.state.save_task(task, idempotency_key="task:schedule-trigger")
        unit_of_work.commit()
    return task


def _schedule(
    task: Task,
    *,
    kind: AssistantScheduleTriggerKind,
    rule: dict[str, object],
    next_fire_at: datetime,
    key: str,
) -> AssistantSchedule:
    return AssistantSchedule.create(
        conversation_id=task.conversation_id,
        task_id=task.id,
        project_id=task.project_id,
        workspace_id=task.workspace_id,
        instruction="Check the project status",
        trigger_kind=kind,
        trigger_rule=rule,
        timezone="Australia/Sydney",
        next_fire_at=next_fire_at,
        permission_profile=PermissionProfile.STANDARD,
        profile_id="local-default",
        timeline_sequence=1,
        idempotency_key=key,
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )


def test_one_shot_trigger_is_idempotent_across_restart_style_sweeps(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "once.db")
    task = _seed(factory)
    due = datetime(2026, 8, 7, 1, 0, tzinfo=UTC)
    schedule = _schedule(
        task,
        kind=AssistantScheduleTriggerKind.ONCE,
        rule={},
        next_fire_at=due,
        key="schedule:once",
    )
    with factory() as unit_of_work:
        unit_of_work.assistant_schedules.create(schedule)
        unit_of_work.commit()

    first = AssistantScheduleTriggerService(
        unit_of_work_factory=factory,
        autostart=False,
    )
    second = AssistantScheduleTriggerService(
        unit_of_work_factory=factory,
        autostart=False,
    )
    assert first.run_once(now=due) == 1
    assert second.run_once(now=due + timedelta(minutes=1)) == 1

    with factory() as unit_of_work:
        persisted = unit_of_work.assistant_schedules.get(schedule.id)
        occurrences = unit_of_work.assistant_schedules.list_occurrences(schedule_id=schedule.id)

    assert persisted is not None
    assert persisted.status is AssistantScheduleStatus.COMPLETED
    assert len(occurrences) == 1
    assert occurrences[0].scheduled_for == due


def test_recurring_catch_up_and_overlap_keep_only_latest_pending_occurrence(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path / "catch-up.db")
    task = _seed(factory)
    first = resolve_first_local_instant(
        local_date=date(2026, 8, 1),
        local_time=time(9),
        timezone="Australia/Sydney",
    )
    schedule = _schedule(
        task,
        kind=AssistantScheduleTriggerKind.DAILY,
        rule={"local_time": "09:00"},
        next_fire_at=first,
        key="schedule:daily",
    )
    with factory() as unit_of_work:
        unit_of_work.assistant_schedules.create(schedule)
        unit_of_work.commit()
    trigger = AssistantScheduleTriggerService(
        unit_of_work_factory=factory,
        autostart=False,
    )

    trigger.run_once(now=first + timedelta(days=2, hours=1))
    trigger.run_once(now=first + timedelta(days=4, hours=1))

    with factory() as unit_of_work:
        occurrences = unit_of_work.assistant_schedules.list_occurrences(schedule_id=schedule.id)
        persisted = unit_of_work.assistant_schedules.get(schedule.id)

    assert len(occurrences) == 1
    assert occurrences[0].status is AssistantOccurrenceStatus.PENDING
    assert occurrences[0].scheduled_for == first + timedelta(days=4)
    assert occurrences[0].coalesced_count == 4
    assert persisted is not None
    assert persisted.next_fire_at == first + timedelta(days=5)


def test_context_attention_pauses_future_triggers_without_replacing_binding(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path / "attention.db")
    task = _seed(factory)
    due = datetime(2026, 8, 7, 1, 0, tzinfo=UTC)
    schedule = _schedule(
        task,
        kind=AssistantScheduleTriggerKind.DAILY,
        rule={"local_time": "11:00"},
        next_fire_at=due,
        key="schedule:attention",
    )
    with factory() as unit_of_work:
        unit_of_work.assistant_schedules.create(schedule)
        unit_of_work.commit()
    trigger = AssistantScheduleTriggerService(
        unit_of_work_factory=factory,
        dispatcher=_AttentionDispatcher(),
        autostart=False,
    )

    assert trigger.run_once(now=due) == 1

    with factory() as unit_of_work:
        persisted = unit_of_work.assistant_schedules.get(schedule.id)
        occurrence = unit_of_work.assistant_schedules.list_occurrences(schedule_id=schedule.id)[0]
    assert persisted is not None
    assert persisted.status is AssistantScheduleStatus.PAUSED
    assert persisted.attention_code == "VERSION_INVALID"
    assert persisted.profile_id == schedule.profile_id
    assert occurrence.status is AssistantOccurrenceStatus.ATTENTION_REQUIRED


def test_claim_fence_rejects_a_stale_worker_after_lease_expiry(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "fence.db")
    task = _seed(factory)
    due = datetime(2026, 8, 7, 1, 0, tzinfo=UTC)
    schedule = _schedule(
        task,
        kind=AssistantScheduleTriggerKind.DAILY,
        rule={"local_time": "11:00"},
        next_fire_at=due,
        key="schedule:fence",
    )
    with factory() as unit_of_work:
        unit_of_work.assistant_schedules.create(schedule)
        unit_of_work.commit()
    with factory() as unit_of_work:
        (first,) = unit_of_work.assistant_schedules.claim_ready(
            worker_id="worker-a",
            now=due,
            lease_until=due + timedelta(seconds=30),
        )
        unit_of_work.commit()
    with factory() as unit_of_work:
        (second,) = unit_of_work.assistant_schedules.claim_ready(
            worker_id="worker-b",
            now=due + timedelta(seconds=31),
            lease_until=due + timedelta(seconds=61),
        )
        unit_of_work.commit()

    assert second.lease_fence == first.lease_fence + 1
    with factory() as unit_of_work:
        stale = replace(
            schedule,
            lease_owner=None,
            lease_until=None,
            lease_fence=first.lease_fence,
        )
        assert unit_of_work.assistant_schedules.settle_claim(first, stale) is None
        assert unit_of_work.assistant_schedules.abandon_claim(second)
        unit_of_work.commit()


def test_three_consecutive_occurrence_failures_pause_a_recurring_schedule(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path / "failures.db")
    task = _seed(factory)
    due = datetime(2026, 8, 7, 1, 0, tzinfo=UTC)
    schedule = _schedule(
        task,
        kind=AssistantScheduleTriggerKind.DAILY,
        rule={"local_time": "11:00"},
        next_fire_at=due + timedelta(days=10),
        key="schedule:failures",
    )
    with factory() as unit_of_work:
        unit_of_work.assistant_schedules.create(schedule)
        unit_of_work.commit()
    trigger = AssistantScheduleTriggerService(
        unit_of_work_factory=factory,
        autostart=False,
    )

    for number in range(3):
        occurrence_id = _create_dispatched_occurrence(
            factory,
            task=task,
            schedule=schedule,
            scheduled_for=due + timedelta(days=number),
            number=number,
        )
        _occurrence, persisted = trigger.record_outcome(
            occurrence_id=occurrence_id,
            status=AssistantOccurrenceStatus.FAILED,
            public_error="The scheduled run failed.",
            now=due + timedelta(days=number, minutes=5),
        )

    assert persisted.status is AssistantScheduleStatus.PAUSED
    assert persisted.consecutive_failures == 3
    assert persisted.attention_code == "CONSECUTIVE_FAILURES"


def _create_dispatched_occurrence(
    factory: SqlAlchemyUnitOfWorkFactory,
    *,
    task: Task,
    schedule: AssistantSchedule,
    scheduled_for: datetime,
    number: int,
):
    turn_id = new_id()
    run = WorkflowRun.create(
        owner_kind="assistant_turn",
        owner_id=str(turn_id),
        conversation_id=task.conversation_id,
        task_id=task.id,
        project_id=task.project_id,
        execution_target=ExecutionTarget.LOCAL,
        trigger_kind=WorkflowTriggerKind.DOMAIN,
        idempotency_key=f"workflow:schedule-failure:{number}",
    )
    node = WorkflowNode.create(
        run_id=run.id,
        plan_revision=1,
        node_key="scheduled-turn",
        kind="assistant.turn",
        payload={},
        public_summary="Run scheduled turn",
    )
    turn = AssistantTurn(
        id=turn_id,
        conversation_id=task.conversation_id,
        task_id=task.id,
        profile_id="local-default",
        scope_digest="0" * 64,
        memory_snapshot_id=new_id(),
        memory_snapshot_hash="1" * 64,
        knowledge_snapshot_id=None,
        knowledge_snapshot_hash=None,
        harness_manifest_id=None,
        harness_manifest_hash=None,
        idempotency_key=f"turn:schedule-failure:{number}",
        workflow_run_id=run.id,
        execution_engine_version=2,
    )
    pending = AssistantScheduleOccurrence.pending(
        schedule_id=schedule.id,
        schedule_revision=schedule.active_revision,
        scheduled_for=scheduled_for,
        now=scheduled_for,
    )
    with factory() as unit_of_work:
        unit_of_work.workflows.create(run, nodes=(node,), edges=())
        unit_of_work.assistant.save_turn(turn)
        persisted = unit_of_work.assistant_schedules.create_occurrence(pending)
        unit_of_work.assistant_schedules.save_occurrence(
            persisted.dispatch(
                turn_id=turn.id,
                workflow_run_id=run.id,
                now=scheduled_for,
            ),
            expected_status=AssistantOccurrenceStatus.PENDING,
        )
        unit_of_work.commit()
    return pending.id
