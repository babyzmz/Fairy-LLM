from __future__ import annotations

import hashlib
from collections.abc import Callable
from uuid import UUID

from fairy_core.memory.retrieval_ports import MemorySnapshotBuilder
from fairy_core.memory.snapshot_builder import DeterministicMemorySnapshotBuilder
from fairy_core.persistence.unit_of_work import CoreUnitOfWork

SnapshotBuilderFactory = Callable[[CoreUnitOfWork], MemorySnapshotBuilder]


def build_default_snapshot(unit_of_work: CoreUnitOfWork) -> MemorySnapshotBuilder:
    return DeterministicMemorySnapshotBuilder(
        memory_repository=unit_of_work.memory,
        search_index=unit_of_work.memory_search,
    )


def snapshot_request_fingerprint(task_id: UUID) -> str:
    return hashlib.sha256(f"task:{task_id}:memory-snapshot:v1".encode()).hexdigest()


__all__ = ["SnapshotBuilderFactory", "build_default_snapshot", "snapshot_request_fingerprint"]
