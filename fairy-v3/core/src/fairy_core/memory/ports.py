from __future__ import annotations

from typing import Protocol
from uuid import UUID

from fairy_core.memory.models import (
    MemoryClaim,
    MemoryClaimRevision,
    MemoryNamespace,
    MemoryObservation,
    MemoryTombstone,
)


class MemoryRepository(Protocol):
    def append_observation(
        self,
        observation: MemoryObservation,
        *,
        request_fingerprint: str,
    ) -> MemoryObservation: ...

    def get_observation(self, observation_id: UUID) -> MemoryObservation | None: ...

    def create_claim(self, claim: MemoryClaim) -> MemoryClaim: ...

    def get_claim(self, claim_id: UUID) -> MemoryClaim | None: ...

    def append_revision(
        self,
        claim_id: UUID,
        *,
        expected_revision: int,
        revision: MemoryClaimRevision,
    ) -> MemoryClaim: ...

    def revisions_for_claim(self, claim_id: UUID) -> list[MemoryClaimRevision]: ...

    def resolve_conflict(
        self,
        claim_id: UUID,
        *,
        expected_revision: int,
        revision: MemoryClaimRevision,
        resolved_claim_ids: tuple[UUID, ...],
    ) -> MemoryClaim: ...

    def forget(self, tombstone: MemoryTombstone) -> MemoryTombstone: ...

    def get_tombstone(
        self,
        *,
        target_id: UUID,
    ) -> MemoryTombstone | None: ...

    def claims_for_scope(
        self,
        *,
        namespace: MemoryNamespace,
        project_id: UUID | None = None,
        conversation_id: UUID | None = None,
        task_id: UUID | None = None,
        device_id: str | None = None,
    ) -> list[MemoryClaim]: ...


__all__ = ["MemoryRepository"]
