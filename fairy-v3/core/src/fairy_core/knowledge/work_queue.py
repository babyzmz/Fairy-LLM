from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

KNOWLEDGE_SYNC_LEASE_DURATION = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class KnowledgeSyncClaim:
    run_id: UUID
    lease_owner: str
    lease_fence: int
    attempts: int
    cancellation_revision: int


__all__ = ["KNOWLEDGE_SYNC_LEASE_DURATION", "KnowledgeSyncClaim"]
