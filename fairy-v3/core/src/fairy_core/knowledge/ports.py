from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from fairy_core.knowledge.catalog import KnowledgeRevisionMetadata
from fairy_core.knowledge.models import (
    HarnessContextManifest,
    KnowledgeCollection,
    KnowledgeIngestItem,
    KnowledgeRevision,
    KnowledgeSnapshot,
    KnowledgeSource,
    KnowledgeSyncDelta,
    KnowledgeSyncRun,
)
from fairy_core.knowledge.work_queue import KnowledgeSyncClaim


class KnowledgeRepository(Protocol):
    def save_source(
        self,
        source: KnowledgeSource,
        collection: KnowledgeCollection,
    ) -> None: ...

    def get_source(self, source_id: UUID) -> KnowledgeSource | None: ...

    def list_sources(self, project_id: UUID) -> tuple[KnowledgeSource, ...]: ...

    def get_collection_for_source(self, source_id: UUID) -> KnowledgeCollection | None: ...

    def list_collections(self, project_id: UUID) -> tuple[KnowledgeCollection, ...]: ...

    def enqueue_sync_run(self, run: KnowledgeSyncRun) -> KnowledgeSyncRun: ...

    def get_sync_run(self, run_id: UUID) -> KnowledgeSyncRun | None: ...

    def resumable_sync_runs(self) -> tuple[KnowledgeSyncRun, ...]: ...

    def claim_next_sync_run(
        self,
        *,
        worker_id: str,
        lease_until: datetime,
    ) -> KnowledgeSyncClaim | None: ...

    def claim_sync_run(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        lease_until: datetime,
    ) -> KnowledgeSyncClaim | None: ...

    def renew_sync_run(
        self,
        claim: KnowledgeSyncClaim,
        *,
        lease_until: datetime,
    ) -> bool: ...

    def settle_sync_run(
        self,
        claim: KnowledgeSyncClaim,
        run: KnowledgeSyncRun,
    ) -> bool: ...

    def abandon_sync_run(self, claim: KnowledgeSyncClaim) -> bool: ...

    def cancel_sync_run(self, run_id: UUID) -> KnowledgeSyncRun: ...

    def synchronize(
        self,
        *,
        source: KnowledgeSource,
        entries: tuple[KnowledgeIngestItem, ...],
    ) -> KnowledgeSyncDelta: ...

    def current_revisions(self, project_id: UUID) -> tuple[KnowledgeRevision, ...]: ...

    def current_revision_metadata(
        self,
        project_id: UUID,
    ) -> tuple[KnowledgeRevisionMetadata, ...]: ...

    def append_snapshot(
        self,
        snapshot: KnowledgeSnapshot,
        *,
        request_fingerprint: str,
    ) -> KnowledgeSnapshot: ...

    def get_snapshot(self, snapshot_id: UUID, *, task_id: UUID) -> KnowledgeSnapshot | None: ...

    def get_snapshot_for_task(self, task_id: UUID) -> KnowledgeSnapshot | None: ...

    def append_manifest(self, manifest: HarnessContextManifest) -> HarnessContextManifest: ...

    def get_manifest(
        self,
        manifest_id: UUID,
        *,
        task_id: UUID,
    ) -> HarnessContextManifest | None: ...

    def get_manifest_for_task(self, task_id: UUID) -> HarnessContextManifest | None: ...

    def revision_from_snapshot(
        self,
        *,
        snapshot_id: UUID,
        task_id: UUID,
        revision_id: UUID,
    ) -> KnowledgeRevision | None: ...

    def search_snapshot(
        self,
        *,
        snapshot_id: UUID,
        task_id: UUID,
        query: str,
        limit: int,
    ) -> tuple[KnowledgeRevision, ...]: ...


__all__ = ["KnowledgeRepository"]
