from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine, RowMapping

from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.domain.ids import new_id
from fairy_core.knowledge.catalog import KnowledgeRevisionMetadata
from fairy_core.knowledge.models import (
    HarnessContextManifest,
    KnowledgeCollection,
    KnowledgeIngestItem,
    KnowledgeRevision,
    KnowledgeSnapshot,
    KnowledgeSnapshotItem,
    KnowledgeSnapshotStatus,
    KnowledgeSource,
    KnowledgeSourceKind,
    KnowledgeSourceStatus,
    KnowledgeSyncDelta,
    KnowledgeSyncRun,
    KnowledgeSyncStatus,
)
from fairy_core.knowledge.repository_records import (
    collection_from_row,
    collection_values,
    manifest_values,
    revision_from_row,
    revision_values,
    snapshot_values,
    source_from_row,
    source_values,
    sync_run_from_row,
    sync_run_values,
)
from fairy_core.knowledge.schema import (
    harness_context_manifests,
    knowledge_collections,
    knowledge_items,
    knowledge_metadata,
    knowledge_revisions,
    knowledge_snapshot_items,
    knowledge_snapshots,
    knowledge_sources,
    knowledge_sync_runs,
)
from fairy_core.knowledge.serialization import manifest_from_row
from fairy_core.knowledge.work_queue import KnowledgeSyncClaim
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.persistence.tenant import normalize_tenant_id

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class SqlAlchemyKnowledgeRepository:
    def __init__(
        self,
        bind: Engine | Connection,
        *,
        tenant_id: str,
        initialize_schema: bool = False,
        owns_engine: bool = False,
    ) -> None:
        if bind.dialect.name not in {"postgresql", "sqlite"}:
            raise ValueError(f"unsupported Knowledge repository dialect: {bind.dialect.name}")
        if initialize_schema and bind.dialect.name != "sqlite":
            raise ValueError("PostgreSQL schemas must be initialized through Alembic")
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._session = SqlAlchemySession(bind, owns_engine=owns_engine)
        if initialize_schema:
            knowledge_metadata.create_all(bind)

    def save_source(
        self,
        source: KnowledgeSource,
        collection: KnowledgeCollection,
    ) -> None:
        if source.id != collection.source_id or source.project_id != collection.project_id:
            raise ValueError("Knowledge Source and Collection do not share a Scope")
        with self._session.write() as connection:
            existing = self._source_row(connection, source.id)
            values = source_values(self._tenant_id, source)
            if existing is None:
                connection.execute(self._insert(knowledge_sources).values(**values))
            else:
                if existing["project_id"] != str(source.project_id):
                    raise VersionConflictError("Knowledge Source Project cannot change")
                connection.execute(
                    update(knowledge_sources)
                    .where(
                        knowledge_sources.c.tenant_id == self._tenant_id,
                        knowledge_sources.c.id == str(source.id),
                    )
                    .values(
                        **{
                            key: value
                            for key, value in values.items()
                            if key
                            not in {
                                "tenant_id",
                                "id",
                                "project_id",
                                "kind",
                                "device_id",
                                "created_at",
                            }
                        }
                    )
                )
            collection_row = (
                connection.execute(
                    select(knowledge_collections).where(
                        knowledge_collections.c.tenant_id == self._tenant_id,
                        knowledge_collections.c.source_id == str(source.id),
                    )
                )
                .mappings()
                .first()
            )
            collection_record = collection_values(self._tenant_id, collection)
            if collection_row is None:
                connection.execute(self._insert(knowledge_collections).values(**collection_record))
            else:
                connection.execute(
                    update(knowledge_collections)
                    .where(
                        knowledge_collections.c.tenant_id == self._tenant_id,
                        knowledge_collections.c.source_id == str(source.id),
                    )
                    .values(
                        **{
                            key: value
                            for key, value in collection_record.items()
                            if key not in {"id", "source_id", "created_at"}
                        }
                    )
                )

    def get_source(self, source_id: UUID) -> KnowledgeSource | None:
        with self._session.read() as connection:
            row = self._source_row(connection, source_id)
        return source_from_row(row) if row is not None else None

    def list_sources(self, project_id: UUID) -> tuple[KnowledgeSource, ...]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(knowledge_sources)
                    .where(
                        knowledge_sources.c.tenant_id == self._tenant_id,
                        knowledge_sources.c.project_id == str(project_id),
                    )
                    .order_by(knowledge_sources.c.created_at, knowledge_sources.c.id)
                )
                .mappings()
                .all()
            )
        return tuple(source_from_row(row) for row in rows)

    def get_collection_for_source(self, source_id: UUID) -> KnowledgeCollection | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(knowledge_collections).where(
                        knowledge_collections.c.tenant_id == self._tenant_id,
                        knowledge_collections.c.source_id == str(source_id),
                    )
                )
                .mappings()
                .first()
            )
        return collection_from_row(row) if row is not None else None

    def list_collections(self, project_id: UUID) -> tuple[KnowledgeCollection, ...]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(knowledge_collections)
                    .where(
                        knowledge_collections.c.tenant_id == self._tenant_id,
                        knowledge_collections.c.project_id == str(project_id),
                    )
                    .order_by(
                        knowledge_collections.c.created_at,
                        knowledge_collections.c.id,
                    )
                )
                .mappings()
                .all()
            )
        return tuple(collection_from_row(row) for row in rows)

    def enqueue_sync_run(self, run: KnowledgeSyncRun) -> KnowledgeSyncRun:
        if run.status is not KnowledgeSyncStatus.QUEUED:
            raise ValueError("Only a queued Knowledge Sync can be enqueued")
        values = sync_run_values(self._tenant_id, run)
        with self._session.write() as connection:
            inserted = connection.execute(
                self._insert(knowledge_sync_runs)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=[
                        knowledge_sync_runs.c.tenant_id,
                        knowledge_sync_runs.c.request_fingerprint,
                    ]
                )
            )
            if inserted.rowcount:
                return run
            row = (
                connection.execute(
                    select(knowledge_sync_runs).where(
                        knowledge_sync_runs.c.tenant_id == self._tenant_id,
                        knowledge_sync_runs.c.request_fingerprint == run.request_fingerprint,
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise VersionConflictError("Knowledge Sync could not be enqueued")
        recovered = sync_run_from_row(row)
        if (
            recovered.source_id != run.source_id
            or recovered.expected_source_revision != run.expected_source_revision
        ):
            raise IdempotencyConflictError("Knowledge Sync replay does not match its request")
        return recovered

    def get_sync_run(self, run_id: UUID) -> KnowledgeSyncRun | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(knowledge_sync_runs).where(
                        knowledge_sync_runs.c.tenant_id == self._tenant_id,
                        knowledge_sync_runs.c.id == str(run_id),
                    )
                )
                .mappings()
                .first()
            )
        return sync_run_from_row(row) if row is not None else None

    def resumable_sync_runs(self) -> tuple[KnowledgeSyncRun, ...]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(knowledge_sync_runs)
                    .where(
                        knowledge_sync_runs.c.tenant_id == self._tenant_id,
                        knowledge_sync_runs.c.status.in_(
                            (
                                KnowledgeSyncStatus.QUEUED.value,
                                KnowledgeSyncStatus.RUNNING.value,
                                KnowledgeSyncStatus.INTERRUPTED.value,
                            )
                        ),
                    )
                    .order_by(knowledge_sync_runs.c.started_at, knowledge_sync_runs.c.id)
                )
                .mappings()
                .all()
            )
        return tuple(sync_run_from_row(row) for row in rows)

    def claim_next_sync_run(
        self,
        *,
        worker_id: str,
        lease_until: datetime,
    ) -> KnowledgeSyncClaim | None:
        return self._claim_sync_run(
            run_id=None,
            worker_id=worker_id,
            lease_until=lease_until,
        )

    def claim_sync_run(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        lease_until: datetime,
    ) -> KnowledgeSyncClaim | None:
        return self._claim_sync_run(
            run_id=run_id,
            worker_id=worker_id,
            lease_until=lease_until,
        )

    def _claim_sync_run(
        self,
        *,
        run_id: UUID | None,
        worker_id: str,
        lease_until: datetime,
    ) -> KnowledgeSyncClaim | None:
        normalized_worker = worker_id.strip()
        if not normalized_worker:
            raise ValueError("worker_id must not be empty")
        now = datetime.now(UTC)
        if lease_until.tzinfo is None or lease_until <= now:
            raise ValueError("lease_until must be a future aware datetime")
        with self._session.write() as connection:
            conditions = [
                knowledge_sync_runs.c.tenant_id == self._tenant_id,
                or_(
                    knowledge_sync_runs.c.status.in_(
                        (
                            KnowledgeSyncStatus.QUEUED.value,
                            KnowledgeSyncStatus.INTERRUPTED.value,
                        )
                    ),
                    and_(
                        knowledge_sync_runs.c.status == KnowledgeSyncStatus.RUNNING.value,
                        knowledge_sync_runs.c.lease_until.is_not(None),
                        knowledge_sync_runs.c.lease_until <= now,
                    ),
                ),
            ]
            if run_id is not None:
                conditions.append(knowledge_sync_runs.c.id == str(run_id))
            statement = (
                select(knowledge_sync_runs)
                .where(*conditions)
                .order_by(knowledge_sync_runs.c.started_at, knowledge_sync_runs.c.id)
                .limit(1)
            )
            if self._session.dialect_name == "postgresql":
                statement = statement.with_for_update(
                    of=knowledge_sync_runs,
                    skip_locked=True,
                )
            row = connection.execute(statement).mappings().first()
            if row is None:
                return None
            previous_fence = int(row["lease_fence"])
            attempts = int(row["attempts"]) + 1
            result = connection.execute(
                update(knowledge_sync_runs)
                .where(
                    knowledge_sync_runs.c.tenant_id == self._tenant_id,
                    knowledge_sync_runs.c.id == row["id"],
                    knowledge_sync_runs.c.lease_fence == previous_fence,
                    knowledge_sync_runs.c.cancellation_revision
                    == int(row["cancellation_revision"]),
                    or_(
                        knowledge_sync_runs.c.status.in_(
                            (
                                KnowledgeSyncStatus.QUEUED.value,
                                KnowledgeSyncStatus.INTERRUPTED.value,
                            )
                        ),
                        and_(
                            knowledge_sync_runs.c.status == KnowledgeSyncStatus.RUNNING.value,
                            knowledge_sync_runs.c.lease_until <= now,
                        ),
                    ),
                )
                .values(
                    status=KnowledgeSyncStatus.RUNNING.value,
                    lease_owner=normalized_worker,
                    lease_until=lease_until,
                    lease_fence=previous_fence + 1,
                    attempts=attempts,
                    error_code=None,
                )
            )
            if result.rowcount != 1:
                return None
        return KnowledgeSyncClaim(
            run_id=UUID(str(row["id"])),
            lease_owner=normalized_worker,
            lease_fence=previous_fence + 1,
            attempts=attempts,
            cancellation_revision=int(row["cancellation_revision"]),
        )

    def renew_sync_run(
        self,
        claim: KnowledgeSyncClaim,
        *,
        lease_until: datetime,
    ) -> bool:
        now = datetime.now(UTC)
        if lease_until.tzinfo is None or lease_until <= now:
            raise ValueError("lease_until must be a future aware datetime")
        with self._session.write() as connection:
            result = connection.execute(
                update(knowledge_sync_runs)
                .where(
                    knowledge_sync_runs.c.tenant_id == self._tenant_id,
                    knowledge_sync_runs.c.id == str(claim.run_id),
                    knowledge_sync_runs.c.status == KnowledgeSyncStatus.RUNNING.value,
                    knowledge_sync_runs.c.lease_owner == claim.lease_owner,
                    knowledge_sync_runs.c.lease_fence == claim.lease_fence,
                    knowledge_sync_runs.c.cancellation_revision == claim.cancellation_revision,
                    knowledge_sync_runs.c.lease_until > now,
                )
                .values(lease_until=lease_until)
            )
        return result.rowcount == 1

    def settle_sync_run(
        self,
        claim: KnowledgeSyncClaim,
        run: KnowledgeSyncRun,
    ) -> bool:
        if run.id != claim.run_id:
            raise ValueError("Knowledge Sync result does not match its claim")
        if run.status not in {
            KnowledgeSyncStatus.COMPLETED,
            KnowledgeSyncStatus.FAILED,
        }:
            raise ValueError("Knowledge Sync result must be completed or failed")
        values = sync_run_values(self._tenant_id, run)
        with self._session.write() as connection:
            result = connection.execute(
                update(knowledge_sync_runs)
                .where(
                    knowledge_sync_runs.c.tenant_id == self._tenant_id,
                    knowledge_sync_runs.c.id == str(claim.run_id),
                    knowledge_sync_runs.c.status == KnowledgeSyncStatus.RUNNING.value,
                    knowledge_sync_runs.c.lease_owner == claim.lease_owner,
                    knowledge_sync_runs.c.lease_fence == claim.lease_fence,
                    knowledge_sync_runs.c.cancellation_revision == claim.cancellation_revision,
                )
                .values(
                    **{
                        key: value
                        for key, value in values.items()
                        if key
                        not in {
                            "tenant_id",
                            "id",
                            "source_id",
                            "project_id",
                            "expected_source_revision",
                            "request_fingerprint",
                            "started_at",
                        }
                    }
                )
            )
        return result.rowcount == 1

    def abandon_sync_run(self, claim: KnowledgeSyncClaim) -> bool:
        with self._session.write() as connection:
            result = connection.execute(
                update(knowledge_sync_runs)
                .where(
                    knowledge_sync_runs.c.tenant_id == self._tenant_id,
                    knowledge_sync_runs.c.id == str(claim.run_id),
                    knowledge_sync_runs.c.status == KnowledgeSyncStatus.RUNNING.value,
                    knowledge_sync_runs.c.lease_owner == claim.lease_owner,
                    knowledge_sync_runs.c.lease_fence == claim.lease_fence,
                    knowledge_sync_runs.c.cancellation_revision == claim.cancellation_revision,
                )
                .values(
                    status=KnowledgeSyncStatus.INTERRUPTED.value,
                    lease_owner=None,
                    lease_until=None,
                    lease_fence=claim.lease_fence + 1,
                    error_code="WORKER_INTERRUPTED",
                )
            )
        return result.rowcount == 1

    def cancel_sync_run(self, run_id: UUID) -> KnowledgeSyncRun:
        now = datetime.now(UTC)
        with self._session.write() as connection:
            row = (
                connection.execute(
                    select(knowledge_sync_runs).where(
                        knowledge_sync_runs.c.tenant_id == self._tenant_id,
                        knowledge_sync_runs.c.id == str(run_id),
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise KeyError(f"Knowledge Sync Run not found: {run_id}")
            if row["status"] not in {
                KnowledgeSyncStatus.COMPLETED.value,
                KnowledgeSyncStatus.FAILED.value,
                KnowledgeSyncStatus.CANCELLED.value,
            }:
                connection.execute(
                    update(knowledge_sync_runs)
                    .where(
                        knowledge_sync_runs.c.tenant_id == self._tenant_id,
                        knowledge_sync_runs.c.id == str(run_id),
                        knowledge_sync_runs.c.cancellation_revision
                        == int(row["cancellation_revision"]),
                    )
                    .values(
                        status=KnowledgeSyncStatus.CANCELLED.value,
                        lease_owner=None,
                        lease_until=None,
                        lease_fence=int(row["lease_fence"]) + 1,
                        cancellation_revision=int(row["cancellation_revision"]) + 1,
                        error_code="KNOWLEDGE_SYNC_CANCELLED",
                        completed_at=now,
                    )
                )
            updated = (
                connection.execute(
                    select(knowledge_sync_runs).where(
                        knowledge_sync_runs.c.tenant_id == self._tenant_id,
                        knowledge_sync_runs.c.id == str(run_id),
                    )
                )
                .mappings()
                .one()
            )
        return sync_run_from_row(updated)

    def synchronize(
        self,
        *,
        source: KnowledgeSource,
        entries: tuple[KnowledgeIngestItem, ...],
    ) -> KnowledgeSyncDelta:
        if source.sync_cursor < 1:
            raise ValueError("Synchronized Knowledge Source requires a positive cursor")
        paths = [entry.relative_path for entry in entries]
        if len(paths) != len(set(paths)):
            raise ValueError("Knowledge synchronization contains duplicate paths")
        created_revisions: list[KnowledgeRevision] = []
        deleted_item_ids: list[UUID] = []
        now = datetime.now(UTC)
        with self._session.write() as connection:
            persisted_source = self._source_row(connection, source.id)
            if persisted_source is None:
                raise KeyError(f"Knowledge Source not found: {source.id}")
            if int(persisted_source["sync_cursor"]) > source.sync_cursor:
                raise VersionConflictError("Knowledge Source cursor moved backwards")
            rows = (
                connection.execute(
                    select(knowledge_items).where(
                        knowledge_items.c.tenant_id == self._tenant_id,
                        knowledge_items.c.source_id == str(source.id),
                    )
                )
                .mappings()
                .all()
            )
            by_path = {str(row["relative_path"]): row for row in rows}
            current_revisions = self._current_revision_rows(connection, source.id)
            revision_by_item = {str(row["item_id"]): row for row in current_revisions}
            incoming_paths = set(paths)
            removed = [row for path, row in by_path.items() if path not in incoming_paths]
            rename_candidates: dict[str, list[RowMapping]] = {}
            for row in removed:
                revision = revision_by_item.get(str(row["id"]))
                if revision is not None:
                    rename_candidates.setdefault(str(revision["content_hash"]), []).append(row)

            consumed_renames: set[str] = set()
            for entry in sorted(entries, key=lambda value: value.relative_path):
                item_row = by_path.get(entry.relative_path)
                if item_row is None:
                    candidates = [
                        row
                        for row in rename_candidates.get(entry.content_hash, ())
                        if str(row["id"]) not in consumed_renames
                    ]
                    if len(candidates) == 1:
                        item_row = candidates[0]
                        consumed_renames.add(str(item_row["id"]))
                        connection.execute(
                            update(knowledge_items)
                            .where(
                                knowledge_items.c.tenant_id == self._tenant_id,
                                knowledge_items.c.id == item_row["id"],
                            )
                            .values(relative_path=entry.relative_path, updated_at=now)
                        )
                    else:
                        item_id = new_id()
                        connection.execute(
                            self._insert(knowledge_items).values(
                                tenant_id=self._tenant_id,
                                id=str(item_id),
                                source_id=str(source.id),
                                project_id=str(source.project_id),
                                relative_path=entry.relative_path,
                                current_revision_id=None,
                                current_revision=0,
                                tombstoned_at=None,
                                created_at=now,
                                updated_at=now,
                            )
                        )
                        item_row = {
                            "id": str(item_id),
                            "current_revision": 0,
                            "current_revision_id": None,
                        }
                previous_revision = revision_by_item.get(str(item_row["id"]))
                if (
                    previous_revision is not None
                    and previous_revision["content_hash"] == entry.content_hash
                    and previous_revision["relative_path"] == entry.relative_path
                ):
                    if item_row.get("tombstoned_at") is not None:
                        connection.execute(
                            update(knowledge_items)
                            .where(
                                knowledge_items.c.tenant_id == self._tenant_id,
                                knowledge_items.c.id == item_row["id"],
                            )
                            .values(tombstoned_at=None, updated_at=now)
                        )
                    continue
                revision_number = int(item_row["current_revision"]) + 1
                revision = KnowledgeRevision.create(
                    item_id=UUID(str(item_row["id"])),
                    source_id=source.id,
                    project_id=source.project_id,
                    revision=revision_number,
                    relative_path=entry.relative_path,
                    title=entry.title,
                    kind=entry.kind,
                    content=entry.content,
                    links=entry.links,
                    frontmatter=entry.frontmatter,
                    provenance=entry.provenance,
                    source_cursor=source.sync_cursor,
                )
                connection.execute(
                    self._insert(knowledge_revisions).values(
                        **revision_values(self._tenant_id, revision)
                    )
                )
                connection.execute(
                    update(knowledge_items)
                    .where(
                        knowledge_items.c.tenant_id == self._tenant_id,
                        knowledge_items.c.id == str(revision.item_id),
                    )
                    .values(
                        relative_path=entry.relative_path,
                        current_revision_id=str(revision.id),
                        current_revision=revision_number,
                        tombstoned_at=None,
                        updated_at=now,
                    )
                )
                created_revisions.append(revision)

            for row in removed:
                if str(row["id"]) in consumed_renames or row["tombstoned_at"] is not None:
                    continue
                connection.execute(
                    update(knowledge_items)
                    .where(
                        knowledge_items.c.tenant_id == self._tenant_id,
                        knowledge_items.c.id == row["id"],
                    )
                    .values(tombstoned_at=now, updated_at=now)
                )
                deleted_item_ids.append(UUID(str(row["id"])))
            connection.execute(
                update(knowledge_sources)
                .where(
                    knowledge_sources.c.tenant_id == self._tenant_id,
                    knowledge_sources.c.id == str(source.id),
                )
                .values(
                    status=source.status.value,
                    revision=source.revision,
                    sync_cursor=source.sync_cursor,
                    display_name=source.display_name,
                    display_path=source.display_path,
                    updated_at=source.updated_at,
                )
            )
        return KnowledgeSyncDelta(
            created_revisions=tuple(created_revisions),
            deleted_item_ids=tuple(deleted_item_ids),
        )

    def current_revisions(self, project_id: UUID) -> tuple[KnowledgeRevision, ...]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(knowledge_revisions)
                    .join(
                        knowledge_items,
                        and_(
                            knowledge_items.c.tenant_id == knowledge_revisions.c.tenant_id,
                            knowledge_items.c.current_revision_id == knowledge_revisions.c.id,
                        ),
                    )
                    .where(
                        knowledge_revisions.c.tenant_id == self._tenant_id,
                        knowledge_revisions.c.project_id == str(project_id),
                        knowledge_items.c.tombstoned_at.is_(None),
                    )
                    .order_by(
                        knowledge_revisions.c.source_id,
                        knowledge_revisions.c.relative_path,
                        knowledge_revisions.c.id,
                    )
                )
                .mappings()
                .all()
            )
        return tuple(revision_from_row(row) for row in rows)

    def current_revision_metadata(
        self,
        project_id: UUID,
    ) -> tuple[KnowledgeRevisionMetadata, ...]:
        revisions = knowledge_revisions.c
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(
                        revisions.id,
                        revisions.source_id,
                        revisions.project_id,
                        revisions.revision,
                        revisions.relative_path,
                        revisions.title,
                        revisions.content_hash,
                        revisions.revision_hash,
                        revisions.byte_length,
                        revisions.links,
                    )
                    .join(
                        knowledge_items,
                        and_(
                            knowledge_items.c.tenant_id == revisions.tenant_id,
                            knowledge_items.c.current_revision_id == revisions.id,
                        ),
                    )
                    .where(
                        revisions.tenant_id == self._tenant_id,
                        revisions.project_id == str(project_id),
                        knowledge_items.c.tombstoned_at.is_(None),
                    )
                    .order_by(revisions.source_id, revisions.relative_path, revisions.id)
                )
                .mappings()
                .all()
            )
        return tuple(
            KnowledgeRevisionMetadata(
                id=UUID(row["id"]),
                source_id=UUID(row["source_id"]),
                project_id=UUID(row["project_id"]),
                revision=int(row["revision"]),
                relative_path=row["relative_path"],
                title=row["title"],
                content_hash=row["content_hash"],
                revision_hash=row["revision_hash"],
                byte_length=int(row["byte_length"]),
                links=tuple(row["links"]),
            )
            for row in rows
        )

    def append_snapshot(
        self,
        snapshot: KnowledgeSnapshot,
        *,
        request_fingerprint: str,
    ) -> KnowledgeSnapshot:
        _digest(request_fingerprint, "request_fingerprint")
        values = snapshot_values(
            self._tenant_id,
            snapshot,
            request_fingerprint=request_fingerprint,
        )
        with self._session.write() as connection:
            inserted = connection.execute(
                self._insert(knowledge_snapshots).values(**values).on_conflict_do_nothing()
            )
            if inserted.rowcount:
                for item in snapshot.items:
                    connection.execute(
                        self._insert(knowledge_snapshot_items).values(
                            tenant_id=self._tenant_id,
                            snapshot_id=str(snapshot.id),
                            ordinal=item.ordinal,
                            item_id=str(item.item_id),
                            revision_id=str(item.revision_id),
                            source_id=str(item.source_id),
                            relative_path=item.relative_path,
                            title=item.title,
                            content_hash=item.content_hash,
                            revision_hash=item.revision_hash,
                        )
                    )
                return snapshot
            row = (
                connection.execute(
                    select(knowledge_snapshots).where(
                        knowledge_snapshots.c.tenant_id == self._tenant_id,
                        knowledge_snapshots.c.request_fingerprint == request_fingerprint,
                    )
                )
                .mappings()
                .first()
            )
            if row is not None:
                recovered = self._snapshot_from_row(connection, row)
                if recovered.content_hash != snapshot.content_hash:
                    raise IdempotencyConflictError(
                        "Knowledge Snapshot replay content does not match"
                    )
                return recovered
            existing = (
                connection.execute(
                    select(knowledge_snapshots).where(
                        knowledge_snapshots.c.tenant_id == self._tenant_id,
                        knowledge_snapshots.c.task_id == str(snapshot.task_id),
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                recovered = self._snapshot_from_row(connection, existing)
                if recovered.content_hash == snapshot.content_hash:
                    return recovered
                raise VersionConflictError("Task already has a different Knowledge Snapshot")
            raise VersionConflictError("Knowledge Snapshot could not be inserted")

    def get_snapshot(self, snapshot_id: UUID, *, task_id: UUID) -> KnowledgeSnapshot | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(knowledge_snapshots).where(
                        knowledge_snapshots.c.tenant_id == self._tenant_id,
                        knowledge_snapshots.c.id == str(snapshot_id),
                        knowledge_snapshots.c.task_id == str(task_id),
                    )
                )
                .mappings()
                .first()
            )
            return self._snapshot_from_row(connection, row) if row is not None else None

    def get_snapshot_for_task(self, task_id: UUID) -> KnowledgeSnapshot | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(knowledge_snapshots).where(
                        knowledge_snapshots.c.tenant_id == self._tenant_id,
                        knowledge_snapshots.c.task_id == str(task_id),
                    )
                )
                .mappings()
                .first()
            )
            return self._snapshot_from_row(connection, row) if row is not None else None

    def append_manifest(self, manifest: HarnessContextManifest) -> HarnessContextManifest:
        values = manifest_values(self._tenant_id, manifest)
        with self._session.write() as connection:
            inserted = connection.execute(
                self._insert(harness_context_manifests).values(**values).on_conflict_do_nothing()
            )
            if inserted.rowcount:
                return manifest
            row = (
                connection.execute(
                    select(harness_context_manifests).where(
                        harness_context_manifests.c.tenant_id == self._tenant_id,
                        harness_context_manifests.c.task_id == str(manifest.task_id),
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise VersionConflictError("Harness Manifest could not be inserted")
            recovered = manifest_from_row(row)
            if recovered.content_hash != manifest.content_hash:
                raise IdempotencyConflictError("Task already has a different Harness Manifest")
            return recovered

    def get_manifest(
        self,
        manifest_id: UUID,
        *,
        task_id: UUID,
    ) -> HarnessContextManifest | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(harness_context_manifests).where(
                        harness_context_manifests.c.tenant_id == self._tenant_id,
                        harness_context_manifests.c.id == str(manifest_id),
                        harness_context_manifests.c.task_id == str(task_id),
                    )
                )
                .mappings()
                .first()
            )
        return manifest_from_row(row) if row is not None else None

    def get_manifest_for_task(self, task_id: UUID) -> HarnessContextManifest | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(harness_context_manifests).where(
                        harness_context_manifests.c.tenant_id == self._tenant_id,
                        harness_context_manifests.c.task_id == str(task_id),
                    )
                )
                .mappings()
                .first()
            )
        return manifest_from_row(row) if row is not None else None

    def revision_from_snapshot(
        self,
        *,
        snapshot_id: UUID,
        task_id: UUID,
        revision_id: UUID,
    ) -> KnowledgeRevision | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(knowledge_revisions)
                    .join(
                        knowledge_snapshot_items,
                        and_(
                            knowledge_snapshot_items.c.tenant_id == knowledge_revisions.c.tenant_id,
                            knowledge_snapshot_items.c.revision_id == knowledge_revisions.c.id,
                        ),
                    )
                    .join(
                        knowledge_snapshots,
                        and_(
                            knowledge_snapshots.c.tenant_id == knowledge_snapshot_items.c.tenant_id,
                            knowledge_snapshots.c.id == knowledge_snapshot_items.c.snapshot_id,
                        ),
                    )
                    .where(
                        knowledge_revisions.c.tenant_id == self._tenant_id,
                        knowledge_revisions.c.id == str(revision_id),
                        knowledge_snapshots.c.id == str(snapshot_id),
                        knowledge_snapshots.c.task_id == str(task_id),
                    )
                )
                .mappings()
                .first()
            )
        return revision_from_row(row) if row is not None else None

    def search_snapshot(
        self,
        *,
        snapshot_id: UUID,
        task_id: UUID,
        query: str,
        limit: int,
    ) -> tuple[KnowledgeRevision, ...]:
        normalized = " ".join(query.casefold().split())
        if not normalized or not 1 <= limit <= 50:
            raise ValueError("Knowledge search requires a query and limit from 1 to 50")
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(knowledge_revisions)
                    .join(
                        knowledge_snapshot_items,
                        and_(
                            knowledge_snapshot_items.c.tenant_id == knowledge_revisions.c.tenant_id,
                            knowledge_snapshot_items.c.revision_id == knowledge_revisions.c.id,
                        ),
                    )
                    .join(
                        knowledge_snapshots,
                        and_(
                            knowledge_snapshots.c.tenant_id == knowledge_snapshot_items.c.tenant_id,
                            knowledge_snapshots.c.id == knowledge_snapshot_items.c.snapshot_id,
                        ),
                    )
                    .where(
                        knowledge_revisions.c.tenant_id == self._tenant_id,
                        knowledge_snapshots.c.id == str(snapshot_id),
                        knowledge_snapshots.c.task_id == str(task_id),
                    )
                    .order_by(knowledge_snapshot_items.c.ordinal)
                )
                .mappings()
                .all()
            )
        matches = [
            revision_from_row(row)
            for row in rows
            if normalized
            in " ".join(
                (
                    str(row["title"]),
                    str(row["relative_path"]),
                    str(row["content"]),
                )
            ).casefold()
        ]
        return tuple(matches[:limit])

    def _source_row(self, connection: Connection, source_id: UUID) -> RowMapping | None:
        return (
            connection.execute(
                select(knowledge_sources).where(
                    knowledge_sources.c.tenant_id == self._tenant_id,
                    knowledge_sources.c.id == str(source_id),
                )
            )
            .mappings()
            .first()
        )

    def _current_revision_rows(
        self,
        connection: Connection,
        source_id: UUID,
    ) -> tuple[RowMapping, ...]:
        return tuple(
            connection.execute(
                select(knowledge_revisions)
                .join(
                    knowledge_items,
                    and_(
                        knowledge_items.c.tenant_id == knowledge_revisions.c.tenant_id,
                        knowledge_items.c.current_revision_id == knowledge_revisions.c.id,
                    ),
                )
                .where(
                    knowledge_revisions.c.tenant_id == self._tenant_id,
                    knowledge_revisions.c.source_id == str(source_id),
                )
            )
            .mappings()
            .all()
        )

    def _snapshot_from_row(
        self,
        connection: Connection,
        row: Mapping[str, Any],
    ) -> KnowledgeSnapshot:
        item_rows = (
            connection.execute(
                select(knowledge_snapshot_items)
                .where(
                    knowledge_snapshot_items.c.tenant_id == self._tenant_id,
                    knowledge_snapshot_items.c.snapshot_id == row["id"],
                )
                .order_by(knowledge_snapshot_items.c.ordinal)
            )
            .mappings()
            .all()
        )
        return KnowledgeSnapshot(
            id=UUID(row["id"]),
            project_id=_uuid(row["project_id"]),
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            source_cursor=int(row["source_cursor"]),
            status=KnowledgeSnapshotStatus(row["status"]),
            degraded_reason=row["degraded_reason"],
            content_hash=row["content_hash"],
            items=tuple(
                KnowledgeSnapshotItem(
                    ordinal=int(item["ordinal"]),
                    item_id=UUID(item["item_id"]),
                    revision_id=UUID(item["revision_id"]),
                    source_id=UUID(item["source_id"]),
                    relative_path=item["relative_path"],
                    title=item["title"],
                    content_hash=item["content_hash"],
                    revision_hash=item["revision_hash"],
                )
                for item in item_rows
            ),
            created_at=row["created_at"],
        )

    def _insert(self, table: Any):
        return (
            postgresql_insert(table)
            if self._session.dialect_name == "postgresql"
            else sqlite_insert(table)
        )

    @staticmethod
    def _source_from_row(row: Mapping[str, Any]) -> KnowledgeSource:
        return KnowledgeSource(
            id=UUID(row["id"]),
            project_id=UUID(row["project_id"]),
            kind=KnowledgeSourceKind(row["kind"]),
            device_id=row["device_id"],
            display_name=row["display_name"],
            display_path=row["display_path"],
            status=KnowledgeSourceStatus(row["status"]),
            revision=int(row["revision"]),
            sync_cursor=int(row["sync_cursor"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _collection_from_row(row: Mapping[str, Any]) -> KnowledgeCollection:
        return KnowledgeCollection(
            id=UUID(row["id"]),
            source_id=UUID(row["source_id"]),
            project_id=UUID(row["project_id"]),
            read_scope=row["read_scope"],
            allowed_directories=tuple(row["allowed_directories"]),
            filters=dict(row["filters"]),
            managed_directory=row["managed_directory"],
            scope_kind=row["scope_kind"],
            revision=int(row["revision"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _revision_from_row(row: Mapping[str, Any]) -> KnowledgeRevision:
        return KnowledgeRevision(
            id=UUID(row["id"]),
            item_id=UUID(row["item_id"]),
            source_id=UUID(row["source_id"]),
            project_id=UUID(row["project_id"]),
            revision=int(row["revision"]),
            relative_path=row["relative_path"],
            title=row["title"],
            kind=row["kind"],
            content=row["content"],
            content_hash=row["content_hash"],
            revision_hash=row["revision_hash"],
            links=tuple(row["links"]),
            frontmatter=dict(row["frontmatter"]),
            provenance=dict(row["provenance"]),
            source_cursor=int(row["source_cursor"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _sync_run_from_row(row: Mapping[str, Any]) -> KnowledgeSyncRun:
        return KnowledgeSyncRun(
            id=UUID(row["id"]),
            source_id=UUID(row["source_id"]),
            project_id=UUID(row["project_id"]),
            status=KnowledgeSyncStatus(row["status"]),
            expected_source_revision=int(row["expected_source_revision"]),
            request_fingerprint=row["request_fingerprint"],
            source_cursor=int(row["source_cursor"]),
            scanned_count=int(row["scanned_count"]),
            changed_count=int(row["changed_count"]),
            deleted_count=int(row["deleted_count"]),
            failed_count=int(row["failed_count"]),
            error_code=row["error_code"],
            lease_owner=row["lease_owner"],
            lease_until=row["lease_until"],
            lease_fence=int(row["lease_fence"]),
            attempts=int(row["attempts"]),
            cancellation_revision=int(row["cancellation_revision"]),
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )


def _uuid(value: str | None) -> UUID | None:
    return UUID(value) if value else None


def _digest(value: str, name: str) -> None:
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def knowledge_request_fingerprint(task_id: UUID) -> str:
    return hashlib.sha256(f"knowledge-snapshot:{task_id}".encode()).hexdigest()


__all__ = ["SqlAlchemyKnowledgeRepository", "knowledge_request_fingerprint"]
