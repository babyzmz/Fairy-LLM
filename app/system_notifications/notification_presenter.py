from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.system_notifications.notification_model import SystemNotification
from app.system_notifications.notification_types import ACTION_REQUIRED, CRITICAL, INFO, SUGGESTION


def level_to_tone(level: str) -> str:
    mapping = {
        ACTION_REQUIRED: "pending",
        CRITICAL: "error",
        SUGGESTION: "warning",
        INFO: "info",
    }
    return mapping.get(level, "info")


def level_label(level: str) -> str:
    mapping = {
        ACTION_REQUIRED: "Action Required",
        CRITICAL: "Critical",
        SUGGESTION: "Suggestion",
        INFO: "Info",
    }
    return mapping.get(level, "Info")


def compact_title(notification: SystemNotification) -> str:
    return f"{level_label(notification.level)} · {notification.title}"


def _payload_value(item: SystemNotification | dict[str, Any], key: str, default: Any = "") -> Any:
    if isinstance(item, SystemNotification):
        return getattr(item, key, default)
    return item.get(key, default)


def _parse_iso(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_active_notification(item: SystemNotification | dict[str, Any], *, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    if bool(_payload_value(item, "is_completed", False)) or bool(_payload_value(item, "is_dismissed", False)):
        return False
    expires_at = _parse_iso(str(_payload_value(item, "expires_at", "")))
    if expires_at and expires_at <= current:
        return False
    snooze_until = _parse_iso(str(_payload_value(item, "snooze_until", "")))
    if snooze_until and snooze_until > current:
        return False
    return True


def is_snoozed_notification(item: SystemNotification | dict[str, Any], *, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    if bool(_payload_value(item, "is_completed", False)) or bool(_payload_value(item, "is_dismissed", False)):
        return False
    snooze_until = _parse_iso(str(_payload_value(item, "snooze_until", "")))
    return bool(snooze_until and snooze_until > current)


def is_resolved_notification(item: SystemNotification | dict[str, Any]) -> bool:
    return bool(_payload_value(item, "is_completed", False)) or bool(_payload_value(item, "is_dismissed", False))


def can_mark_completed(item: SystemNotification | dict[str, Any]) -> bool:
    level = str(_payload_value(item, "level", INFO) or INFO)
    return level in {SUGGESTION, INFO}


def notification_matches_filter(item: SystemNotification | dict[str, Any], filter_name: str, *, now: datetime | None = None) -> bool:
    name = (filter_name or "active").strip().lower()
    level = str(_payload_value(item, "level", INFO) or INFO)
    if name == "all":
        return True
    if name == "active":
        return is_active_notification(item, now=now)
    if name == "action_required":
        return is_active_notification(item, now=now) and level == ACTION_REQUIRED
    if name == "critical":
        return is_active_notification(item, now=now) and level == CRITICAL
    if name == "snoozed":
        return is_snoozed_notification(item, now=now)
    if name in {"resolved", "completed"}:
        return is_resolved_notification(item)
    return is_active_notification(item, now=now)


def notification_counts(items: list[SystemNotification | dict[str, Any]]) -> dict[str, int]:
    current = datetime.now(timezone.utc)
    counts = {
        "all": len(items),
        "active": 0,
        "action_required": 0,
        "critical": 0,
        "snoozed": 0,
        "resolved": 0,
    }
    for item in items:
        level = str(_payload_value(item, "level", INFO) or INFO)
        if is_resolved_notification(item):
            counts["resolved"] += 1
            continue
        if is_snoozed_notification(item, now=current):
            counts["snoozed"] += 1
            continue
        if is_active_notification(item, now=current):
            counts["active"] += 1
            if level == ACTION_REQUIRED:
                counts["action_required"] += 1
            if level == CRITICAL:
                counts["critical"] += 1
    return counts
