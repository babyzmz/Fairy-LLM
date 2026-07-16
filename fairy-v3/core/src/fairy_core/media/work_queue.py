from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

MEDIA_WORK_LEASE_DURATION = timedelta(seconds=30)
MEDIA_COMMAND_LEASE_DURATION = timedelta(seconds=20)


@dataclass(frozen=True, slots=True)
class MediaWorkClaim:
    job_id: UUID
    lease_owner: str
    lease_fence: int
    attempts: int


__all__ = [
    "MEDIA_COMMAND_LEASE_DURATION",
    "MEDIA_WORK_LEASE_DURATION",
    "MediaWorkClaim",
]
