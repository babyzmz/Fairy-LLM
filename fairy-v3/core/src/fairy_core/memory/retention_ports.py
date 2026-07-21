from __future__ import annotations

from datetime import datetime
from typing import Protocol

from fairy_core.memory.retention_models import (
    MemoryPayloadPurgeResult,
    MemoryRetentionCandidate,
)


class MemoryRetentionRepository(Protocol):
    def retention_candidates(
        self,
        *,
        before: datetime,
        limit: int = 1_000,
    ) -> tuple[MemoryRetentionCandidate, ...]: ...

    def purge_forgotten_payloads(
        self,
        *,
        before: datetime,
        limit: int = 1_000,
    ) -> MemoryPayloadPurgeResult: ...


__all__ = ["MemoryRetentionRepository"]
