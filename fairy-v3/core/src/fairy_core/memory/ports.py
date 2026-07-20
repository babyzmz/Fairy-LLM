from __future__ import annotations

from typing import Protocol
from uuid import UUID

from fairy_core.memory.models import (
    MemoryClaim,
    MemoryClaimRevision,
    MemoryNamespace,
    MemoryObservation,
    MemoryTargetKind,
    MemoryTombstone,
    ObservationStatus,
)


class MemoryRepository(Protocol):
    def append_observation(
        self,
        observation: MemoryObservation,
        *,
        request_fingerprint: str,
    ) -> MemoryObservation: ...

    def get_observation(
        self,
        observation_id: UUID,
        *,
        include_forgotten: bool = False,
    ) -> MemoryObservation | None: ...

    def transition_observation(
        self,
        observation_id: UUID,
        *,
        expected_status: ObservationStatus,
        status: ObservationStatus,
    ) -> MemoryObservation: ...

    def create_claim(
        self,
        claim: MemoryClaim,
        *,
        request_fingerprint: str,
    ) -> MemoryClaim: ...

    def get_claim(
        self,
        claim_id: UUID,
        *,
        include_forgotten: bool = False,
    ) -> MemoryClaim | None: ...

    def append_revision(
        self,
        claim_id: UUID,
        *,
        expected_revision: int,
        revision: MemoryClaimRevision,
        request_fingerprint: str,
    ) -> MemoryClaim: ...

    def revisions_for_claim(self, claim_id: UUID) -> list[MemoryClaimRevision]: ...

    def resolve_conflict(
        self,
        claim_id: UUID,
        *,
        expected_revision: int,
        revision: MemoryClaimRevision,
        resolved_claim_ids: tuple[UUID, ...],
        request_fingerprint: str,
    ) -> MemoryClaim: ...

    def forget(
        self,
        tombstone: MemoryTombstone,
        *,
        request_fingerprint: str,
    ) -> MemoryTombstone: ...

    def get_tombstone(
        self,
        *,
        target_kind: MemoryTargetKind,
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

    def observations_for_scope(
        self,
        *,
        namespace: MemoryNamespace,
        project_id: UUID | None = None,
        conversation_id: UUID | None = None,
        task_id: UUID | None = None,
        limit: int | None = None,
        newest_first: bool = False,
        retrievable_only: bool = False,
    ) -> list[MemoryObservation]: ...

    def claims_for_projection(self) -> list[MemoryClaim]: ...

    def observations_for_projection(self) -> list[MemoryObservation]: ...


__all__ = ["MemoryRepository"]
