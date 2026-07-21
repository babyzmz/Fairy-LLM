from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import yaml

from fairy_core.commanding.models import EventVisibility
from fairy_core.contracts.knowledge import KnowledgeSyncStartInput
from fairy_core.contracts.obsidian import (
    ObsidianSourceCreateInput,
    ObsidianSourceListInput,
    ObsidianSourceModel,
    ObsidianSourceSyncInput,
    ObsidianSyncResultModel,
    ObsidianVaultItemReadInput,
)
from fairy_core.domain.errors import VersionConflictError
from fairy_core.knowledge.models import (
    KnowledgeCollection,
    KnowledgeIngestItem,
    KnowledgeSource,
    KnowledgeSourceKind,
    KnowledgeSourceStatus,
    KnowledgeSyncRun,
    KnowledgeSyncStatus,
)
from fairy_core.knowledge.work_queue import KnowledgeSyncClaim
from fairy_core.obsidian import ObsidianConnector
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import CancellationToken, ProviderCancelledError

_MAX_FRONTMATTER_BYTES = 64 * 1024


class ObsidianKnowledgeSync:
    def __init__(
        self,
        *,
        connector: ObsidianConnector,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        device_id: str = "local-device",
    ) -> None:
        self._connector = connector
        self._unit_of_work_factory = unit_of_work_factory
        self._device_id = device_id

    def create_source(self, request: ObsidianSourceCreateInput) -> ObsidianSourceModel:
        model = self._connector.create_source(request)
        source, collection = self._domain_source(model)
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.knowledge.save_source(source, collection)
            payload = {
                "source_id": str(source.id),
                "revision": source.revision,
                "status": source.status.value,
            }
            if not unit_of_work.commands.has_domain_event(
                event_type="knowledge.source.updated",
                project_id=source.project_id,
                conversation_id=None,
                payload=payload,
            ):
                unit_of_work.commands.append_domain_event(
                    event_type="knowledge.source.updated",
                    visibility=EventVisibility.USER,
                    message="Knowledge source connected",
                    payload=payload,
                    actor="user",
                    project_id=source.project_id,
                )
            unit_of_work.commit()
        return model

    def enqueue(self, request: KnowledgeSyncStartInput) -> KnowledgeSyncRun:
        with self._unit_of_work_factory() as unit_of_work:
            persisted = unit_of_work.knowledge.get_source(request.source_id)
            if persisted is None:
                raise KeyError(f"Knowledge Source not found: {request.source_id}")
            if persisted.revision != request.expected_revision:
                raise VersionConflictError("Knowledge Source changed concurrently")
            fingerprint = hashlib.sha256(
                f"knowledge-sync:{request.idempotency_key}".encode()
            ).hexdigest()
            run = unit_of_work.knowledge.enqueue_sync_run(
                KnowledgeSyncRun.enqueue(
                    source_id=persisted.id,
                    project_id=persisted.project_id,
                    expected_source_revision=request.expected_revision,
                    request_fingerprint=fingerprint,
                    source_cursor=persisted.sync_cursor,
                )
            )
            payload = {
                "run_id": str(run.id),
                "source_id": str(run.source_id),
                "status": run.status.value,
            }
            if not unit_of_work.commands.has_domain_event(
                event_type="knowledge.sync.started",
                project_id=run.project_id,
                conversation_id=None,
                payload=payload,
            ):
                unit_of_work.commands.append_domain_event(
                    event_type="knowledge.sync.started",
                    visibility=EventVisibility.USER,
                    message="Knowledge sync started",
                    payload=payload,
                    actor="core",
                    project_id=run.project_id,
                )
            unit_of_work.commit()
        return run

    def get(self, run_id) -> KnowledgeSyncRun:
        with self._unit_of_work_factory() as unit_of_work:
            run = unit_of_work.knowledge.get_sync_run(run_id)
        if run is None:
            raise KeyError(f"Knowledge Sync Run not found: {run_id}")
        return run

    def execute(
        self,
        claim: KnowledgeSyncClaim,
        cancellation: CancellationToken,
    ) -> KnowledgeSyncRun:
        run = self.get(claim.run_id)
        if (
            run.status is not KnowledgeSyncStatus.RUNNING
            or run.lease_owner != claim.lease_owner
            or run.lease_fence != claim.lease_fence
            or run.cancellation_revision != claim.cancellation_revision
        ):
            raise ProviderCancelledError("Knowledge Sync lease is no longer active")
        cancellation.raise_if_cancelled()

        try:
            connector_source = self._connector_source(run)
            if connector_source.revision == run.expected_source_revision:
                result = self._connector.sync(
                    ObsidianSourceSyncInput(
                        source_id=run.source_id,
                        expected_revision=run.expected_source_revision,
                    )
                )
                connector_source = result.source
                connector_failed_count = result.failed_count
            elif connector_source.revision == run.expected_source_revision + 1:
                connector_failed_count = 1 if connector_source.status == "partial" else 0
            else:
                raise VersionConflictError("Obsidian source advanced beyond this Sync Run")
            cancellation.raise_if_cancelled()
            page = self._connector.list_items(run.source_id)
            entries: list[KnowledgeIngestItem] = []
            read_failures = 0
            for item in page.items:
                cancellation.raise_if_cancelled()
                try:
                    content = self._connector.read_item(
                        ObsidianVaultItemReadInput(
                            source_id=run.source_id,
                            relative_path=item.relative_path,
                            expected_source_revision=page.source_revision,
                            expected_content_hash=item.content_hash,
                        )
                    )
                    entries.append(
                        KnowledgeIngestItem(
                            relative_path=item.relative_path,
                            title=item.title,
                            kind=item.kind,
                            content=content.content,
                            content_hash=item.content_hash,
                            links=item.links,
                            frontmatter=_frontmatter(content.content),
                            provenance={
                                "source_type": "obsidian",
                                "source_id": str(run.source_id),
                                "relative_path": item.relative_path,
                                "source_revision": page.source_revision,
                                "modified_at": item.modified_at.isoformat(),
                                "content_hash": item.content_hash,
                            },
                        )
                    )
                except (OSError, UnicodeError, ValueError):
                    read_failures += 1
            cancellation.raise_if_cancelled()
            failed_count = connector_failed_count + read_failures
            source, collection = self._domain_source(
                connector_source,
                sync_cursor=page.source_revision,
                status=(
                    KnowledgeSourceStatus.PARTIAL if failed_count else KnowledgeSourceStatus.READY
                ),
            )
            with self._unit_of_work_factory() as unit_of_work:
                unit_of_work.knowledge.save_source(source, collection)
                delta = unit_of_work.knowledge.synchronize(
                    source=source,
                    entries=tuple(entries),
                )
                completed = replace(
                    run,
                    status=KnowledgeSyncStatus.COMPLETED,
                    source_cursor=source.sync_cursor,
                    scanned_count=len(entries),
                    changed_count=delta.changed_count,
                    deleted_count=delta.deleted_count,
                    failed_count=failed_count,
                    error_code=None,
                    lease_owner=None,
                    lease_until=None,
                    attempts=claim.attempts,
                    completed_at=datetime.now(UTC),
                )
                if not unit_of_work.knowledge.settle_sync_run(claim, completed):
                    raise ProviderCancelledError("Knowledge Sync lease was cancelled")
                for revision in delta.created_revisions:
                    unit_of_work.commands.append_domain_event(
                        event_type="knowledge.item.revision.created",
                        visibility=EventVisibility.DEVELOPER,
                        message="Knowledge item revision created",
                        payload={
                            "run_id": str(run.id),
                            "source_id": str(source.id),
                            "item_id": str(revision.item_id),
                            "revision_id": str(revision.id),
                            "revision": revision.revision,
                            "content_hash": revision.content_hash,
                            "revision_hash": revision.revision_hash,
                            "source_cursor": revision.source_cursor,
                        },
                        actor="core",
                        project_id=source.project_id,
                    )
                for item_id in delta.deleted_item_ids:
                    unit_of_work.commands.append_domain_event(
                        event_type="knowledge.item.revision.deleted",
                        visibility=EventVisibility.DEVELOPER,
                        message="Knowledge item deleted from its source",
                        payload={
                            "run_id": str(run.id),
                            "source_id": str(source.id),
                            "item_id": str(item_id),
                            "source_cursor": source.sync_cursor,
                        },
                        actor="core",
                        project_id=source.project_id,
                    )
                unit_of_work.commands.append_domain_event(
                    event_type="knowledge.sync.completed",
                    visibility=EventVisibility.USER,
                    message="Knowledge sync completed",
                    payload={
                        "run_id": str(run.id),
                        "source_id": str(source.id),
                        "source_cursor": completed.source_cursor,
                        "scanned_count": completed.scanned_count,
                        "changed_count": completed.changed_count,
                        "deleted_count": completed.deleted_count,
                        "failed_count": completed.failed_count,
                    },
                    actor="core",
                    project_id=source.project_id,
                )
                unit_of_work.commit()
            return completed
        except ProviderCancelledError:
            raise
        except Exception as error:
            with self._unit_of_work_factory() as unit_of_work:
                failed = replace(
                    run,
                    status=KnowledgeSyncStatus.FAILED,
                    failed_count=max(1, run.failed_count),
                    error_code=str(getattr(error, "error_code", "KNOWLEDGE_SYNC_FAILED"))[:128],
                    lease_owner=None,
                    lease_until=None,
                    attempts=claim.attempts,
                    completed_at=datetime.now(UTC),
                )
                settled = unit_of_work.knowledge.settle_sync_run(claim, failed)
                if settled:
                    unit_of_work.commands.append_domain_event(
                        event_type="knowledge.sync.failed",
                        visibility=EventVisibility.USER,
                        message="Knowledge sync failed",
                        payload={
                            "run_id": str(run.id),
                            "source_id": str(run.source_id),
                            "error_code": failed.error_code,
                            "failed_count": failed.failed_count,
                        },
                        actor="core",
                        project_id=run.project_id,
                    )
                    unit_of_work.commit()
                    return failed
            raise

    def result(self, run: KnowledgeSyncRun) -> ObsidianSyncResultModel:
        if run.status is not KnowledgeSyncStatus.COMPLETED:
            raise RuntimeError(f"Knowledge Sync did not complete: {run.status.value}")
        source = self._connector_source(run)
        return ObsidianSyncResultModel(
            source=source,
            scanned_count=run.scanned_count,
            changed_count=run.changed_count,
            deleted_count=run.deleted_count,
            failed_count=run.failed_count,
        )

    def _connector_source(self, run: KnowledgeSyncRun) -> ObsidianSourceModel:
        page = self._connector.list_sources(ObsidianSourceListInput(project_id=run.project_id))
        for source in page.items:
            if source.id == run.source_id:
                return source
        raise KeyError(f"Obsidian source not found: {run.source_id}")

    def _domain_source(
        self,
        model: ObsidianSourceModel,
        *,
        sync_cursor: int = 0,
        status: KnowledgeSourceStatus | None = None,
    ) -> tuple[KnowledgeSource, KnowledgeCollection]:
        source = KnowledgeSource(
            id=model.id,
            project_id=model.project_id,
            kind=KnowledgeSourceKind.OBSIDIAN,
            device_id=self._device_id,
            display_name=model.display_name,
            display_path=model.vault_display_path,
            status=status or KnowledgeSourceStatus(model.status),
            revision=model.revision,
            sync_cursor=sync_cursor,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
        collection = KnowledgeCollection.create(
            source_id=model.id,
            project_id=model.project_id,
            read_scope=model.read_scope.value,
            allowed_directories=model.allowed_directories,
            managed_directory=model.managed_directory,
        )
        return source, collection


def _frontmatter(content: str) -> Mapping[str, Any]:
    if not content.startswith("---\n"):
        return {}
    boundary = content.find("\n---", 4, _MAX_FRONTMATTER_BYTES)
    if boundary < 0:
        return {}
    try:
        parsed = yaml.safe_load(content[4:boundary])
    except yaml.YAMLError:
        return {}
    if not isinstance(parsed, Mapping):
        return {}
    return _safe_json_mapping(parsed)


def _safe_json_mapping(value: Mapping[Any, Any], *, depth: int = 0) -> dict[str, Any]:
    if depth > 6:
        return {}
    result: dict[str, Any] = {}
    for raw_key, raw_value in list(value.items())[:128]:
        key = str(raw_key)[:200]
        if isinstance(raw_value, Mapping):
            result[key] = _safe_json_mapping(raw_value, depth=depth + 1)
        elif isinstance(raw_value, list):
            result[key] = [
                (
                    item
                    if isinstance(item, (str, int, bool))
                    or item is None
                    or (isinstance(item, float) and math.isfinite(item))
                    else str(item)
                )
                for item in raw_value[:128]
            ]
        elif (
            isinstance(raw_value, (str, int, bool))
            or raw_value is None
            or (isinstance(raw_value, float) and math.isfinite(raw_value))
        ):
            result[key] = raw_value
        else:
            result[key] = str(raw_value)
    return result


__all__ = ["ObsidianKnowledgeSync"]
