from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from fairy_core.assistant.schedule_models import (
    AssistantSchedule,
    AssistantScheduleTriggerKind,
)
from fairy_core.assistant.schedule_recurrence import (
    advance_due_schedule,
    next_schedule_instant,
    resolve_first_local_instant,
    validate_schedule_rule,
)
from fairy_core.commanding.types import PermissionProfile


def _schedule(
    *,
    kind: AssistantScheduleTriggerKind,
    rule: dict[str, object],
    next_fire_at: datetime,
) -> AssistantSchedule:
    return AssistantSchedule.create(
        conversation_id=UUID(int=1),
        workspace_id=UUID(int=2),
        instruction="Run a scheduled check",
        trigger_kind=kind,
        trigger_rule=rule,
        timezone="Australia/Sydney",
        next_fire_at=next_fire_at,
        permission_profile=PermissionProfile.STANDARD,
        profile_id="local-default",
        timeline_sequence=1,
        idempotency_key=f"schedule:{kind.value}",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_daily_schedule_skips_to_first_valid_time_during_spring_dst() -> None:
    first = resolve_first_local_instant(
        local_date=date(2026, 10, 3),
        local_time=time(2, 30),
        timezone="Australia/Sydney",
    )
    schedule = _schedule(
        kind=AssistantScheduleTriggerKind.DAILY,
        rule={"local_time": "02:30"},
        next_fire_at=first,
    )

    next_fire = next_schedule_instant(schedule, after=first)

    assert next_fire == datetime(2026, 10, 3, 16, 0, tzinfo=UTC)


def test_ambiguous_fall_dst_time_runs_only_the_first_occurrence() -> None:
    ambiguous = resolve_first_local_instant(
        local_date=date(2026, 4, 5),
        local_time=time(2, 30),
        timezone="Australia/Sydney",
    )

    assert ambiguous == datetime(2026, 4, 4, 15, 30, tzinfo=UTC)


def test_weekday_and_interval_rules_preserve_their_distinct_semantics() -> None:
    friday = resolve_first_local_instant(
        local_date=date(2026, 8, 7),
        local_time=time(9),
        timezone="Australia/Sydney",
    )
    weekday = _schedule(
        kind=AssistantScheduleTriggerKind.WEEKDAYS,
        rule={"local_time": "09:00"},
        next_fire_at=friday,
    )
    interval = _schedule(
        kind=AssistantScheduleTriggerKind.INTERVAL,
        rule={"amount": 6, "unit": "hours"},
        next_fire_at=friday,
    )

    assert next_schedule_instant(weekday, after=friday) == friday + timedelta(days=3)
    assert next_schedule_instant(interval, after=friday) == friday + timedelta(hours=6)


def test_catch_up_keeps_only_the_latest_due_instant_and_missed_count() -> None:
    first = resolve_first_local_instant(
        local_date=date(2026, 8, 1),
        local_time=time(9),
        timezone="Australia/Sydney",
    )
    schedule = _schedule(
        kind=AssistantScheduleTriggerKind.DAILY,
        rule={"local_time": "09:00"},
        next_fire_at=first,
    )

    advance = advance_due_schedule(
        schedule,
        now=resolve_first_local_instant(
            local_date=date(2026, 8, 3),
            local_time=time(10),
            timezone="Australia/Sydney",
        ),
    )

    assert advance is not None
    assert advance.latest_due_at == first + timedelta(days=2)
    assert advance.next_fire_at == first + timedelta(days=3)
    assert advance.missed_count == 2


def test_rule_validation_rejects_cron_shaped_or_invalid_rules() -> None:
    schedule = _schedule(
        kind=AssistantScheduleTriggerKind.INTERVAL,
        rule={"amount": 1, "unit": "hours"},
        next_fire_at=datetime(2026, 8, 7, tzinfo=UTC),
    )
    validate_schedule_rule(schedule)

    invalid = replace(schedule, trigger_rule={"cron": "* * * * *"})
    try:
        validate_schedule_rule(invalid)
    except ValueError as error:
        assert "amount" in str(error)
    else:
        raise AssertionError("Cron-shaped rule unexpectedly passed validation")
