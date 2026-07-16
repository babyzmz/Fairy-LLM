from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

ASSISTANT_TURN_LEASE_DURATION = timedelta(seconds=30)
ASSISTANT_COMMAND_LEASE_DURATION = timedelta(seconds=20)


def assistant_command_lease_until() -> datetime:
    return datetime.now(UTC) + ASSISTANT_COMMAND_LEASE_DURATION


@dataclass(frozen=True, slots=True)
class AssistantTurnWorkClaim:
    turn_id: UUID
    request_revision: int
    lease_owner: str
    lease_until: datetime
    lease_fence: int
    attempts: int


__all__ = [
    "ASSISTANT_COMMAND_LEASE_DURATION",
    "ASSISTANT_TURN_LEASE_DURATION",
    "AssistantTurnWorkClaim",
    "assistant_command_lease_until",
]
