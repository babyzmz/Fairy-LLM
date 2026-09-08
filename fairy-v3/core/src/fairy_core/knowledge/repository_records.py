from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from fairy_core.knowledge.models import (
    HarnessContextManifest,
    KnowledgeCollection,
    KnowledgeRevision,
    KnowledgeSnapshot,
    KnowledgeSource,
    KnowledgeSourceKind,
    KnowledgeSourceStatus,
    KnowledgeSyncRun,
    KnowledgeSyncStatus,
)


def source_values(tenant_id: str, source: KnowledgeSource) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "id": str(source.id),
        "project_id": str(source.project_id),
        "kind": source.kind.value,
        "device_id": source.device_id,
        "display_name": source.display_name,
        "display_path": source.display_path,
        "status": source.status.value,
        "revision": source.revision,
        "sync_cursor": source.sync_cursor,
        "created_at": source.created_at,
        "updated_at": source.updated_at,
    }


def collection_values(tenant_id: str, collection: KnowledgeCollection) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "id": str(collection.id),
        "source_id": str(collection.source_id),
        "project_id": str(collection.project_id),
        "read_scope": collection.read_scope,
        "allowed_directories": list(collection.allowed_directories),
        "filters": dict(collection.filters),
        "managed_directory": collection.managed_directory,
        "scope_kind": collection.scope_kind,
        "revision": collection.revision,
        "created_at": collection.created_at,
        "updated_at": collection.updated_at,
    }


def revision_values(tenant_id: str, revision: KnowledgeRevision) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "id": str(revision.id),
        "item_id": str(revision.item_id),
        "source_id": str(revision.source_id),
        "project_id": str(revision.project_id),
        "revision": revision.revision,
        "relative_path": revision.relative_path,
        "title": revision.title,
        "kind": revision.kind,
        "content": revision.content,
        "byte_length": len(revision.content.encode("utf-8")),
        "content_hash": revision.content_hash,
        "revision_hash": revision.revision_hash,
        "links": list(revision.links),
        "frontmatter": dict(revision.frontmatter),
        "provenance": dict(revision.provenance),
        "source_cursor": revision.source_cursor,
        "created_at": revision.created_at,
    }


def snapshot_values(
    tenant_id: str,
    snapshot: KnowledgeSnapshot,
    *,
    request_fingerprint: str,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "id": str(snapshot.id),
        "project_id": str(snapshot.project_id) if snapshot.project_id else None,
        "conversation_id": str(snapshot.conversation_id),
        "task_id": str(snapshot.task_id),
        "source_cursor": snapshot.source_cursor,
        "status": snapshot.status.value,
        "degraded_reason": snapshot.degraded_reason,
        "content_hash": snapshot.content_hash,
        "request_fingerprint": request_fingerprint,
        "created_at": snapshot.created_at,
    }


def manifest_values(tenant_id: str, manifest: HarnessContextManifest) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "id": str(manifest.id),
        "task_id": str(manifest.task_id),
        "scope_digest": manifest.scope_digest,
        "workspace_id": str(manifest.workspace_id),
        "workspace_version_id": (
            str(manifest.workspace_version_id) if manifest.workspace_version_id else None
        ),
        "memory_snapshot_id": str(manifest.memory_snapshot_id),
        "memory_snapshot_hash": manifest.memory_snapshot_hash,
        "knowledge_snapshot_id": str(manifest.knowledge_snapshot_id),
        "knowledge_snapshot_hash": manifest.knowledge_snapshot_hash,
        "tool_registry_generation": manifest.tool_registry_generation,
        "tool_registry_digest": manifest.tool_registry_digest,
        "tool_definitions": [
            snapshot.canonical_payload() for snapshot in manifest.tool_definitions
        ],
        "skill_package_digests": list(manifest.skill_package_digests),
        "mcp_capability_snapshot": list(manifest.mcp_capability_snapshot),
        "model_selection": dict(manifest.model_selection),
        "budget": dict(manifest.budget),
        "persona_version": manifest.persona_version,
        "persona_digest": manifest.persona_digest,
        "persona_instruction": manifest.persona_instruction,
        "content_hash": manifest.content_hash,
        "created_at": manifest.created_at,
    }


def sync_run_values(tenant_id: str, run: KnowledgeSyncRun) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "id": str(run.id),
        "source_id": str(run.source_id),
        "project_id": str(run.project_id),
        "status": run.status.value,
        "expected_source_revision": run.expected_source_revision,
        "request_fingerprint": run.request_fingerprint,
        "source_cursor": run.source_cursor,
        "scanned_count": run.scanned_count,
        "changed_count": run.changed_count,
        "deleted_count": run.deleted_count,
        "failed_count": run.failed_count,
        "error_code": run.error_code,
        "lease_owner": run.lease_owner,
        "lease_until": run.lease_until,
        "lease_fence": run.lease_fence,
        "attempts": run.attempts,
        "cancellation_revision": run.cancellation_revision,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }


def source_from_row(row: Mapping[str, Any]) -> KnowledgeSource:
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


def collection_from_row(row: Mapping[str, Any]) -> KnowledgeCollection:
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


def revision_from_row(row: Mapping[str, Any]) -> KnowledgeRevision:
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


def sync_run_from_row(row: Mapping[str, Any]) -> KnowledgeSyncRun:
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


__all__ = [
    "collection_from_row",
    "collection_values",
    "manifest_values",
    "revision_from_row",
    "revision_values",
    "snapshot_values",
    "source_from_row",
    "source_values",
    "sync_run_from_row",
    "sync_run_values",
]
