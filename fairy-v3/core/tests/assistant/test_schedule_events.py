from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from fairy_core.assistant.schedule_application import AssistantScheduleApplication
from fairy_core.assistant.schedule_trigger import AssistantScheduleTriggerService
from fairy_core.commanding.sqlalchemy import SqlAlchemyCommandLedger
from fairy_core.contracts.assistant_schedules import AssistantScheduleCreateInput
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from tests.assistant.test_schedule_repository import _schedule, _seed


def test_schedule_changes_are_durable_scoped_and_replays_do_not_duplicate_events(tmp_path):
    path = tmp_path / "events.db"
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    task = _seed(factory)
    app = AssistantScheduleApplication(
        unit_of_work_factory=factory,
        trigger=SimpleNamespace(wake=lambda: None),
    )
    request = AssistantScheduleCreateInput(
        conversation_id=task.conversation_id,
        instruction="Private instruction not for events",
        operation_mode="answer",
        trigger_kind="daily",
        trigger_rule={"local_time": "09:00"},
        timezone="Australia/Sydney",
        next_fire_at=datetime.now(UTC) + timedelta(days=1),
        profile_id="local-default",
        idempotency_key="schedule-events",
    )
    created = app.create(request, profile_id="local-default", model_selection=None)
    assert app.create(request, profile_id="local-default", model_selection=None).id == created.id
    paused = app.pause(created.id, expected_revision=created.active_revision)
    with pytest.raises(ValueError):
        app.resume(created.id, expected_revision=999)
    resumed = app.resume(created.id, expected_revision=paused.active_revision)
    app.cancel(created.id, expected_revision=resumed.active_revision)
    engine.dispose()
    reopened = create_sqlite_core_engine(path)
    try:
        with SqlAlchemyUnitOfWorkFactory(reopened, tenant_id="local")() as unit:
            events = unit.commands.events_after(cursor=0)
        assert [item.event_type for item in events] == ["assistant.schedule.changed"] * 4
        assert [item.payload["status"] for item in events] == [
            "active",
            "paused",
            "active",
            "cancelled",
        ]
        assert all(item.conversation_id == task.conversation_id for item in events)
        assert all(item.project_id == task.project_id for item in events)
        assert all("instruction" not in str(item.payload) for item in events)
        with SqlAlchemyUnitOfWorkFactory(reopened, tenant_id="other")() as unit:
            assert unit.commands.events_after(cursor=0) == []
    finally:
        reopened.dispose()


def test_schedule_event_failure_rolls_back_state_and_wake(tmp_path, monkeypatch):
    engine = create_sqlite_core_engine(tmp_path / "rollback.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    task = _seed(factory)
    schedule = _schedule(task)
    with factory() as unit:
        unit.assistant_schedules.create(schedule)
        unit.commit()
    app = AssistantScheduleApplication(
        unit_of_work_factory=factory,
        trigger=SimpleNamespace(wake=lambda: None),
    )

    def reject_event(*args, **kwargs):
        raise RuntimeError("Injected ledger failure")

    monkeypatch.setattr(SqlAlchemyCommandLedger, "append_domain_event", reject_event)
    before = factory.ledger_signal.version
    try:
        with pytest.raises(RuntimeError, match="Injected"):
            app.pause(schedule.id, expected_revision=1)
        assert app.get(schedule.id).status.value == "active"
        assert factory.ledger_signal.version == before
    finally:
        engine.dispose()


def test_trigger_emits_due_change_but_not_repeated_pending_lease_changes(tmp_path):
    engine = create_sqlite_core_engine(tmp_path / "trigger-events.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    task = _seed(factory)
    schedule = _schedule(task)
    with factory() as unit:
        unit.assistant_schedules.create(schedule)
        unit.commit()
    trigger = AssistantScheduleTriggerService(unit_of_work_factory=factory, autostart=False)
    try:
        trigger.run_once(now=schedule.next_fire_at)
        trigger.run_once(now=schedule.next_fire_at + timedelta(seconds=1))
        with factory() as unit:
            events = unit.commands.events_after(cursor=0)
        assert len(events) == 1
        assert events[0].event_type == "assistant.schedule.changed"
        assert events[0].payload["occurrence_status"] == "pending"
        assert events[0].conversation_id == task.conversation_id
    finally:
        trigger.close()
        engine.dispose()
