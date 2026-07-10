from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine, RowMapping

from fairy_core.domain.errors import (
    IdempotencyConflictError,
    MemoryConflictError,
)
from fairy_core.memory.models import MemoryAuthority, MemoryNamespace
from fairy_core.memory.retrieval_models import (
    MemorySelectionReason,
    MemorySnapshot,
    MemorySnapshotItem,
    MemorySnapshotStatus,
    MemorySourceKind,
    ProjectionState,
)
from fairy_core.memory.schema import (
    memory_metadata,
    memory_snapshot_items,
    memory_snapshots,
)
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.persistence.tenant import normalize_tenant_id

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class SqlAlchemyMemorySnapshotRepository:
    def __init__(
        self,
        bind: Engine | Connection,
        *,
        tenant_id: str,
        initialize_schema: bool = False,
        owns_engine: bool = False,
    ) -> None:
        dialect_name = bind.dialect.name
        if dialect_name not in {"postgresql", "sqlite"}:
            raise ValueError(f"unsupported Snapshot repository dialect: {dialect_name}")
        if initialize_schema and dialect_name != "sqlite":
            raise ValueError("PostgreSQL schemas must be initialized through Alembic")
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._session = SqlAlchemySession(bind, owns_engine=owns_engine)
        if initialize_schema:
            memory_metadata.create_all(bind)

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def close(self) -> None:
        self._session.close()

    def append(
        self,
        snapshot: MemorySnapshot,
        *,
        request_fingerprint: str,
    ) -> MemorySnapshot:
        request_fingerprint = _validate_request_fingerprint(request_fingerprint)
        content_fingerprint = self._content_fingerprint(snapshot)
        values = self._snapshot_values(
            snapshot,
            request_fingerprint=request_fingerprint,
            content_fingerprint=content_fingerprint,
        )
        with self._session.write() as connection:
            result = connection.execute(
                self._insert(memory_snapshots).values(**values).on_conflict_do_nothing()
            )
            if result.rowcount:
                for item in snapshot.items:
                    connection.execute(
                        self._insert(memory_snapshot_items).values(
                            **self._item_values(snapshot.id, item)
                        )
                    )
                return snapshot

            by_request = self._row_by_request(connection, request_fingerprint)
            if by_request is not None:
                if by_request["content_fingerprint"] != content_fingerprint:
                    raise IdempotencyConflictError("memory Snapshot replay content does not match")
                return self._snapshot_from_row(connection, by_request)

            by_task = self._row_by_task(connection, snapshot.task_id)
            if by_task is not None:
                if by_task["content_fingerprint"] == content_fingerprint:
                    return self._snapshot_from_row(connection, by_task)
                raise MemoryConflictError("Task already has a different memory Snapshot")

            if self._row_by_id(connection, snapshot.id) is not None:
                raise MemoryConflictError("memory Snapshot ID is already in use")
            raise MemoryConflictError("memory Snapshot could not be inserted")

    def get(
        self,
        snapshot_id: UUID,
        *,
        task_id: UUID,
    ) -> MemorySnapshot | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(memory_snapshots).where(
                        memory_snapshots.c.tenant_id == self._tenant_id,
                        memory_snapshots.c.id == str(snapshot_id),
                        memory_snapshots.c.task_id == str(task_id),
                    )
                )
                .mappings()
                .first()
            )
            return self._snapshot_from_row(connection, row) if row is not None else None

    def get_for_task(self, task_id: UUID) -> MemorySnapshot | None:
        with self._session.read() as connection:
            row = self._row_by_task(connection, task_id)
            return self._snapshot_from_row(connection, row) if row is not None else None

    def _row_by_id(self, connection: Connection, snapshot_id: UUID) -> RowMapping | None:
        return (
            connection.execute(
                select(memory_snapshots).where(
                    memory_snapshots.c.tenant_id == self._tenant_id,
                    memory_snapshots.c.id == str(snapshot_id),
                )
            )
            .mappings()
            .first()
        )

    def _row_by_task(self, connection: Connection, task_id: UUID) -> RowMapping | None:
        return (
            connection.execute(
                select(memory_snapshots).where(
                    memory_snapshots.c.tenant_id == self._tenant_id,
                    memory_snapshots.c.task_id == str(task_id),
                )
            )
            .mappings()
            .first()
        )

    def _row_by_request(
        self,
        connection: Connection,
        request_fingerprint: str,
    ) -> RowMapping | None:
        return (
            connection.execute(
                select(memory_snapshots).where(
                    memory_snapshots.c.tenant_id == self._tenant_id,
                    memory_snapshots.c.request_fingerprint == request_fingerprint,
                )
            )
            .mappings()
            .first()
        )

    def _snapshot_from_row(
        self,
        connection: Connection,
        row: Mapping[str, Any],
    ) -> MemorySnapshot:
        item_rows = (
            connection.execute(
                select(memory_snapshot_items)
                .where(
                    memory_snapshot_items.c.tenant_id == self._tenant_id,
                    memory_snapshot_items.c.snapshot_id == row["id"],
                )
                .order_by(memory_snapshot_items.c.ordinal)
            )
            .mappings()
            .all()
        )
        items = tuple(self._item_from_row(item) for item in item_rows)
        return MemorySnapshot.restore(
            id=UUID(row["id"]),
            project_id=_uuid(row["project_id"]),
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            base_version_id=_uuid(row["base_version_id"]),
            target_version_id=_uuid(row["target_version_id"]),
            snapshot_version=int(row["snapshot_version"]),
            policy_version=row["policy_version"],
            source_watermark_cursor=int(row["source_watermark_cursor"]),
            projection_generation=int(row["projection_generation"]),
            projection_watermark_cursor=int(row["projection_watermark_cursor"]),
            projection_state=ProjectionState(row["projection_state"]),
            status=MemorySnapshotStatus(row["status"]),
            degraded_reason=row["degraded_reason"],
            content_hash=row["content_hash"],
            token_count=int(row["token_count"]),
            items=items,
            created_at=row["created_at"],
        )

    def _insert(self, table: Any):
        return (
            postgresql_insert(table)
            if self._session.dialect_name == "postgresql"
            else sqlite_insert(table)
        )

    def _snapshot_values(
        self,
        snapshot: MemorySnapshot,
        *,
        request_fingerprint: str,
        content_fingerprint: str,
    ) -> dict[str, Any]:
        return {
            "tenant_id": self._tenant_id,
            "id": str(snapshot.id),
            "project_id": str(snapshot.project_id) if snapshot.project_id else None,
            "conversation_id": str(snapshot.conversation_id),
            "task_id": str(snapshot.task_id),
            "base_version_id": (
                str(snapshot.base_version_id) if snapshot.base_version_id else None
            ),
            "target_version_id": (
                str(snapshot.target_version_id) if snapshot.target_version_id else None
            ),
            "snapshot_version": snapshot.snapshot_version,
            "policy_version": snapshot.policy_version,
            "source_watermark_cursor": snapshot.source_watermark_cursor,
            "projection_generation": snapshot.projection_generation,
            "projection_watermark_cursor": snapshot.projection_watermark_cursor,
            "projection_state": snapshot.projection_state.value,
            "status": snapshot.status.value,
            "degraded_reason": snapshot.degraded_reason,
            "content_hash": snapshot.content_hash,
            "token_count": snapshot.token_count,
            "request_fingerprint": request_fingerprint,
            "content_fingerprint": content_fingerprint,
            "created_at": snapshot.created_at,
        }

    def _item_values(
        self,
        snapshot_id: UUID,
        item: MemorySnapshotItem,
    ) -> dict[str, Any]:
        return {
            "tenant_id": self._tenant_id,
            "snapshot_id": str(snapshot_id),
            "ordinal": item.ordinal,
            "source_kind": item.source_kind.value,
            "source_id": str(item.source_id),
            "source_revision": item.source_revision,
            "namespace": item.namespace.value if item.namespace else None,
            "selection_reason": item.selection_reason.value,
            "authority": item.authority.value,
            "score_components": dict(item.score_components),
            "rendered_text": item.rendered_text,
            "rendered_text_hash": item.rendered_text_hash,
            "token_count": item.token_count,
        }

    @staticmethod
    def _item_from_row(row: Mapping[str, Any]) -> MemorySnapshotItem:
        return MemorySnapshotItem(
            ordinal=int(row["ordinal"]),
            source_kind=MemorySourceKind(row["source_kind"]),
            source_id=UUID(row["source_id"]),
            source_revision=(
                int(row["source_revision"]) if row["source_revision"] is not None else None
            ),
            namespace=MemoryNamespace(row["namespace"]) if row["namespace"] else None,
            selection_reason=MemorySelectionReason(row["selection_reason"]),
            authority=MemoryAuthority(row["authority"]),
            score_components=dict(row["score_components"]),
            rendered_text=row["rendered_text"],
            rendered_text_hash=row["rendered_text_hash"],
            token_count=int(row["token_count"]),
        )

    @staticmethod
    def _content_fingerprint(snapshot: MemorySnapshot) -> str:
        payload = {
            "project_id": str(snapshot.project_id) if snapshot.project_id else None,
            "conversation_id": str(snapshot.conversation_id),
            "task_id": str(snapshot.task_id),
            "base_version_id": (
                str(snapshot.base_version_id) if snapshot.base_version_id else None
            ),
            "target_version_id": (
                str(snapshot.target_version_id) if snapshot.target_version_id else None
            ),
            "content_hash": snapshot.content_hash,
            "token_count": snapshot.token_count,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


def _uuid(value: str | None) -> UUID | None:
    return UUID(value) if value else None


def _validate_request_fingerprint(value: str) -> str:
    if _SHA256_HEX.fullmatch(value) is None:
        raise ValueError("request_fingerprint must be a lowercase SHA-256 digest")
    return value


__all__ = ["SqlAlchemyMemorySnapshotRepository"]
