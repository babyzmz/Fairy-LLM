from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fairy_core.assistant.schedule_models import (
    AssistantSchedule,
    AssistantScheduleTriggerKind,
)

_MAX_CATCH_UP_STEPS = 100_000


@dataclass(frozen=True, slots=True)
class AssistantScheduleAdvance:
    latest_due_at: datetime
    next_fire_at: datetime | None
    missed_count: int


def advance_due_schedule(
    schedule: AssistantSchedule,
    *,
    now: datetime,
) -> AssistantScheduleAdvance | None:
    normalized_now = _aware_utc(now)
    due = _aware_utc(schedule.next_fire_at)
    if due > normalized_now:
        return None
    if schedule.trigger_kind is AssistantScheduleTriggerKind.ONCE:
        return AssistantScheduleAdvance(
            latest_due_at=due,
            next_fire_at=None,
            missed_count=0,
        )

    count = 0
    latest_due = due
    while due <= normalized_now:
        latest_due = due
        count += 1
        if count > _MAX_CATCH_UP_STEPS:
            raise ValueError("Assistant schedule catch-up window is too large")
        due = next_schedule_instant(schedule, after=due)
    return AssistantScheduleAdvance(
        latest_due_at=latest_due,
        next_fire_at=due,
        missed_count=count - 1,
    )


def next_schedule_instant(schedule: AssistantSchedule, *, after: datetime) -> datetime:
    if schedule.trigger_kind is AssistantScheduleTriggerKind.ONCE:
        raise ValueError("One-shot Assistant schedules do not recur")
    zone = _zone(schedule.timezone)
    previous = _aware_utc(after)
    local_previous = previous.astimezone(zone)
    rule = schedule.trigger_rule

    if schedule.trigger_kind is AssistantScheduleTriggerKind.INTERVAL:
        amount = _positive_int(rule.get("amount"), name="interval amount", maximum=8760)
        unit = str(rule.get("unit", "")).strip().lower()
        if unit == "hours":
            return previous + timedelta(hours=amount)
        if unit == "days":
            candidate = datetime.combine(
                local_previous.date() + timedelta(days=amount),
                local_previous.timetz().replace(tzinfo=None),
            )
            return _resolve_local(candidate, zone)
        raise ValueError("Assistant schedule interval unit must be hours or days")

    local_time = _rule_time(rule)
    if schedule.trigger_kind is AssistantScheduleTriggerKind.DAILY:
        candidate_date = local_previous.date()
        candidate = datetime.combine(candidate_date, local_time)
        if _resolve_local(candidate, zone) <= previous:
            candidate = datetime.combine(candidate_date + timedelta(days=1), local_time)
        return _resolve_local(candidate, zone)

    if schedule.trigger_kind is AssistantScheduleTriggerKind.WEEKDAYS:
        candidate_date = local_previous.date()
        while True:
            candidate = datetime.combine(candidate_date, local_time)
            if candidate_date.weekday() < 5 and _resolve_local(candidate, zone) > previous:
                return _resolve_local(candidate, zone)
            candidate_date += timedelta(days=1)

    if schedule.trigger_kind is AssistantScheduleTriggerKind.WEEKLY:
        weekday = _weekday(rule.get("weekday"))
        candidate_date = local_previous.date()
        days_ahead = (weekday - candidate_date.weekday()) % 7
        candidate_date += timedelta(days=days_ahead)
        candidate = datetime.combine(candidate_date, local_time)
        if _resolve_local(candidate, zone) <= previous:
            candidate = datetime.combine(candidate_date + timedelta(days=7), local_time)
        return _resolve_local(candidate, zone)

    raise ValueError("Assistant schedule trigger kind is unsupported")


def validate_schedule_rule(schedule: AssistantSchedule) -> None:
    _zone(schedule.timezone)
    kind = schedule.trigger_kind
    if kind is AssistantScheduleTriggerKind.ONCE:
        return
    if kind in {
        AssistantScheduleTriggerKind.DAILY,
        AssistantScheduleTriggerKind.WEEKDAYS,
    }:
        _rule_time(schedule.trigger_rule)
        return
    if kind is AssistantScheduleTriggerKind.WEEKLY:
        _rule_time(schedule.trigger_rule)
        _weekday(schedule.trigger_rule.get("weekday"))
        return
    if kind is AssistantScheduleTriggerKind.INTERVAL:
        _positive_int(
            schedule.trigger_rule.get("amount"),
            name="interval amount",
            maximum=8760,
        )
        if str(schedule.trigger_rule.get("unit", "")).strip().lower() not in {
            "hours",
            "days",
        }:
            raise ValueError("Assistant schedule interval unit must be hours or days")
        return
    raise ValueError("Assistant schedule trigger kind is unsupported")


def resolve_first_local_instant(
    *,
    local_date: date,
    local_time: time,
    timezone: str,
) -> datetime:
    return _resolve_local(datetime.combine(local_date, local_time), _zone(timezone))


def _resolve_local(candidate: datetime, zone: ZoneInfo) -> datetime:
    if candidate.tzinfo is not None:
        raise ValueError("Local schedule candidate must be naive")
    probe = candidate
    for _ in range(24 * 60 + 1):
        valid: list[datetime] = []
        for fold in (0, 1):
            localized = probe.replace(tzinfo=zone, fold=fold)
            utc_value = localized.astimezone(UTC)
            roundtrip = utc_value.astimezone(zone)
            if roundtrip.replace(tzinfo=None) == probe and roundtrip.fold == fold:
                valid.append(utc_value)
        if valid:
            return min(valid)
        probe += timedelta(minutes=1)
    raise ValueError("Assistant schedule local time could not be resolved")


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("Assistant schedule timezone is unavailable") from error


def _rule_time(rule: object) -> time:
    if not hasattr(rule, "get"):
        raise ValueError("Assistant schedule trigger rule is invalid")
    value = str(rule.get("local_time", "")).strip()  # type: ignore[union-attr]
    try:
        parsed = time.fromisoformat(value)
    except ValueError as error:
        raise ValueError("Assistant schedule local time is invalid") from error
    if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
        raise ValueError("Assistant schedule local time must use HH:MM")
    return parsed


def _weekday(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("Assistant schedule weekday is invalid")
    try:
        weekday = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError("Assistant schedule weekday is invalid") from error
    if weekday < 0 or weekday > 6:
        raise ValueError("Assistant schedule weekday is invalid")
    return weekday


def _positive_int(value: object, *, name: str, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Assistant schedule {name} is invalid")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError(f"Assistant schedule {name} is invalid") from error
    if parsed < 1 or parsed > maximum:
        raise ValueError(f"Assistant schedule {name} is invalid")
    return parsed


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Assistant schedule timestamps must be timezone-aware")
    return value.astimezone(UTC)


__all__ = [
    "AssistantScheduleAdvance",
    "advance_due_schedule",
    "next_schedule_instant",
    "resolve_first_local_instant",
    "validate_schedule_rule",
]
