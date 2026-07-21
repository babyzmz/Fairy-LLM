from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from fairy_core.memory.models import MemoryTargetKind


@dataclass(frozen=True, slots=True)
class MemoryRetentionCandidate:
    target_kind: MemoryTargetKind
    target_id: UUID
    source_event_id: UUID
    project_id: UUID | None
    conversation_id: UUID | None
    task_id: UUID | None
    version_id: UUID | None
    stale_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryPayloadPurgeResult:
    observations: int
    claims: int

    @property
    def total(self) -> int:
        return self.observations + self.claims


__all__ = ["MemoryPayloadPurgeResult", "MemoryRetentionCandidate"]
