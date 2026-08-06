from __future__ import annotations

from datetime import UTC, datetime, timedelta

ASSISTANT_COMMAND_LEASE_DURATION = timedelta(seconds=20)


def assistant_command_lease_until() -> datetime:
    return datetime.now(UTC) + ASSISTANT_COMMAND_LEASE_DURATION


__all__ = ["ASSISTANT_COMMAND_LEASE_DURATION", "assistant_command_lease_until"]
