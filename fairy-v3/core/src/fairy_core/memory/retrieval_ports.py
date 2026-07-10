from __future__ import annotations

from typing import Protocol
from uuid import UUID

from fairy_core.domain.models import ScopeContract
from fairy_core.memory.retrieval_models import (
    MemoryProjectionHealth,
    MemorySearchDocument,
    MemorySearchHit,
    MemorySnapshot,
    MemorySourceKind,
)


class MemorySearchIndex(Protocol):
    def search(
        self,
        *,
        scope: ScopeContract,
        query: str,
        generation: int,
        limit: int,
    ) -> tuple[MemorySearchHit, ...]: ...

    def health(self, *, source_watermark_cursor: int) -> MemoryProjectionHealth: ...


class MemoryProjectionWriter(Protocol):
    def upsert_documents(
        self,
        documents: tuple[MemorySearchDocument, ...],
    ) -> None: ...

    def remove_source(
        self,
        *,
        source_kind: MemorySourceKind,
        source_id: UUID,
    ) -> None: ...

    def advance_checkpoint(
        self,
        *,
        generation: int,
        source_watermark_cursor: int,
    ) -> MemoryProjectionHealth: ...


class MemorySnapshotRepository(Protocol):
    def append(
        self,
        snapshot: MemorySnapshot,
        *,
        request_fingerprint: str,
    ) -> MemorySnapshot: ...

    def get(
        self,
        snapshot_id: UUID,
        *,
        task_id: UUID,
    ) -> MemorySnapshot | None: ...

    def get_for_task(self, task_id: UUID) -> MemorySnapshot | None: ...


class MemorySnapshotBuilder(Protocol):
    def build(
        self,
        *,
        scope: ScopeContract,
        query: str,
        source_watermark_cursor: int,
    ) -> MemorySnapshot: ...


__all__ = [
    "MemoryProjectionWriter",
    "MemorySearchIndex",
    "MemorySnapshotBuilder",
    "MemorySnapshotRepository",
]
