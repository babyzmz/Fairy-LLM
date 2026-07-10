from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine

from fairy_core.domain.errors import MemoryConflictError, MemoryProjectionStaleError
from fairy_core.domain.models import ScopeContract
from fairy_core.memory.retrieval_models import (
    MemoryProjectionHealth,
    MemorySearchDocument,
    MemorySearchHit,
    MemorySourceKind,
    ProjectionState,
)
from fairy_core.memory.schema import (
    memory_projection_checkpoints,
    memory_search_documents,
)
from fairy_core.memory.search_queries import (
    build_postgres_search_statement,
    build_sqlite_search_statement,
    hit_from_row,
    query_tokens,
    validate_search_bounds,
)
from fairy_core.memory.sqlite_fts import sqlite_fts_available
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.persistence.tenant import normalize_tenant_id

_PROJECTION_SCHEMA_VERSION = "hermes-lexical-v1"


class SqlAlchemyMemorySearchIndex:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str) -> None:
        _validate_dialect(bind)
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._session = SqlAlchemySession(bind)

    def search(
        self,
        *,
        scope: ScopeContract,
        query: str,
        generation: int,
        limit: int,
    ) -> tuple[MemorySearchHit, ...]:
        tokens = query_tokens(query)
        validate_search_bounds(generation=generation, limit=limit)
        if not tokens:
            return ()
        with self._session.read() as connection:
            if connection.dialect.name == "sqlite":
                if not sqlite_fts_available(connection):
                    raise MemoryProjectionStaleError("SQLite FTS5 projection is unavailable")
                statement = build_sqlite_search_statement(
                    scope=scope,
                    tokens=tokens,
                    generation=generation,
                    limit=limit,
                    tenant_id=self._tenant_id,
                )
                dialect_name = "sqlite"
            else:
                statement = build_postgres_search_statement(
                    scope=scope,
                    query=query,
                    generation=generation,
                    limit=limit,
                    tenant_id=self._tenant_id,
                )
                dialect_name = "postgresql"
            rows = connection.execute(statement).mappings().all()
        normalized_query = " ".join(tokens)
        hits = [
            hit_from_row(
                row,
                normalized_query=normalized_query,
                dialect_name=dialect_name,
            )
            for row in rows
        ]
        hits.sort(
            key=lambda hit: (
                not hit.exact_match,
                -hit.lexical_score,
                -hit.document.source_cursor,
                hit.document.source_kind.value,
                str(hit.document.source_id),
            )
        )
        return tuple(hits[:limit])

    def health(
        self,
        *,
        generation: int,
        source_watermark_cursor: int,
    ) -> MemoryProjectionHealth:
        if generation < 1:
            raise ValueError("generation must be positive")
        if source_watermark_cursor < 0:
            raise ValueError("source_watermark_cursor cannot be negative")
        with self._session.read() as connection:
            if connection.dialect.name == "sqlite" and not sqlite_fts_available(connection):
                return _unavailable_health(
                    generation=generation,
                    source_watermark_cursor=source_watermark_cursor,
                )
            row = (
                connection.execute(
                    select(memory_projection_checkpoints).where(
                        memory_projection_checkpoints.c.tenant_id == self._tenant_id,
                        memory_projection_checkpoints.c.generation == generation,
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return _unavailable_health(
                generation=generation,
                source_watermark_cursor=source_watermark_cursor,
            )
        projected = int(row["projected_watermark_cursor"])
        stored_state = ProjectionState(row["state"])
        if stored_state is ProjectionState.READY and projected < source_watermark_cursor:
            state = ProjectionState.STALE
            error_code = "MEMORY_PROJECTION_STALE"
        elif stored_state is ProjectionState.READY:
            state = ProjectionState.READY
            error_code = None
        else:
            state = stored_state
            error_code = row["last_error_code"]
        return MemoryProjectionHealth(
            generation=generation,
            state=state,
            source_watermark_cursor=source_watermark_cursor,
            projected_watermark_cursor=projected,
            last_error_code=error_code,
            updated_at=_datetime(row["updated_at"]),
        )


class SqlAlchemyMemoryProjectionWriter:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str) -> None:
        _validate_dialect(bind)
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._session = SqlAlchemySession(bind)

    def upsert_documents(
        self,
        documents: tuple[MemorySearchDocument, ...],
    ) -> None:
        if not documents:
            return
        with self._session.write() as connection:
            _require_projection_available(connection)
            for document in documents:
                fts_rowid = _fts_rowid_for_document(
                    connection,
                    tenant_id=self._tenant_id,
                    document=document,
                )
                statement = _insert_for(connection, memory_search_documents).values(
                    **_document_values(
                        self._tenant_id,
                        document,
                        fts_rowid=fts_rowid,
                    )
                )
                excluded = statement.excluded
                statement = statement.on_conflict_do_update(
                    index_elements=[
                        memory_search_documents.c.tenant_id,
                        memory_search_documents.c.projection_generation,
                        memory_search_documents.c.source_kind,
                        memory_search_documents.c.source_id,
                        memory_search_documents.c.source_revision,
                    ],
                    set_={
                        "namespace": excluded.namespace,
                        "fts_rowid": func.coalesce(
                            memory_search_documents.c.fts_rowid,
                            excluded.fts_rowid,
                        ),
                        "project_id": excluded.project_id,
                        "conversation_id": excluded.conversation_id,
                        "task_id": excluded.task_id,
                        "version_id": excluded.version_id,
                        "language": excluded.language,
                        "normalized_text": excluded.normalized_text,
                        "content_hash": excluded.content_hash,
                        "source_cursor": excluded.source_cursor,
                        "updated_at": excluded.updated_at,
                    },
                    where=or_(
                        excluded.source_cursor > memory_search_documents.c.source_cursor,
                        and_(
                            excluded.source_cursor == memory_search_documents.c.source_cursor,
                            excluded.content_hash == memory_search_documents.c.content_hash,
                        ),
                    ),
                )
                connection.execute(statement)
                stored = connection.execute(
                    select(
                        memory_search_documents.c.source_cursor,
                        memory_search_documents.c.content_hash,
                    ).where(
                        memory_search_documents.c.tenant_id == self._tenant_id,
                        memory_search_documents.c.projection_generation
                        == document.projection_generation,
                        memory_search_documents.c.source_kind == document.source_kind.value,
                        memory_search_documents.c.source_id == str(document.source_id),
                        memory_search_documents.c.source_revision
                        == (document.source_revision or 0),
                    )
                ).one()
                if (
                    int(stored.source_cursor) == document.source_cursor
                    and stored.content_hash != document.content_hash
                ):
                    raise MemoryConflictError("projection source cursor has conflicting content")

    def remove_source(
        self,
        *,
        source_kind: MemorySourceKind,
        source_id: UUID,
    ) -> None:
        with self._session.write() as connection:
            connection.execute(
                delete(memory_search_documents).where(
                    memory_search_documents.c.tenant_id == self._tenant_id,
                    memory_search_documents.c.source_kind == source_kind.value,
                    memory_search_documents.c.source_id == str(source_id),
                )
            )

    def advance_checkpoint(
        self,
        *,
        generation: int,
        source_watermark_cursor: int,
    ) -> MemoryProjectionHealth:
        if generation < 1:
            raise ValueError("generation must be positive")
        if source_watermark_cursor < 0:
            raise ValueError("source_watermark_cursor cannot be negative")
        now = datetime.now(UTC)
        with self._session.write() as connection:
            _require_projection_available(connection)
            statement = _insert_for(connection, memory_projection_checkpoints).values(
                tenant_id=self._tenant_id,
                generation=generation,
                source_watermark_cursor=source_watermark_cursor,
                projected_watermark_cursor=source_watermark_cursor,
                state=ProjectionState.READY.value,
                schema_version=_PROJECTION_SCHEMA_VERSION,
                retry_count=0,
                last_error_code=None,
                updated_at=now,
            )
            excluded = statement.excluded
            greatest = func.greatest if connection.dialect.name == "postgresql" else func.max
            statement = statement.on_conflict_do_update(
                index_elements=[
                    memory_projection_checkpoints.c.tenant_id,
                    memory_projection_checkpoints.c.generation,
                ],
                set_={
                    "source_watermark_cursor": greatest(
                        memory_projection_checkpoints.c.source_watermark_cursor,
                        excluded.source_watermark_cursor,
                    ),
                    "projected_watermark_cursor": greatest(
                        memory_projection_checkpoints.c.projected_watermark_cursor,
                        excluded.projected_watermark_cursor,
                    ),
                    "state": ProjectionState.READY.value,
                    "schema_version": _PROJECTION_SCHEMA_VERSION,
                    "retry_count": 0,
                    "last_error_code": None,
                    "updated_at": now,
                },
            )
            connection.execute(statement)
            row = (
                connection.execute(
                    select(memory_projection_checkpoints).where(
                        memory_projection_checkpoints.c.tenant_id == self._tenant_id,
                        memory_projection_checkpoints.c.generation == generation,
                    )
                )
                .mappings()
                .one()
            )
        projected = int(row["projected_watermark_cursor"])
        return MemoryProjectionHealth(
            generation=generation,
            state=ProjectionState.READY,
            source_watermark_cursor=min(source_watermark_cursor, projected),
            projected_watermark_cursor=projected,
            last_error_code=None,
            updated_at=_datetime(row["updated_at"]),
        )


def _document_values(
    tenant_id: str,
    document: MemorySearchDocument,
    *,
    fts_rowid: int,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "id": str(document.id),
        "fts_rowid": fts_rowid,
        "source_kind": document.source_kind.value,
        "source_id": str(document.source_id),
        "source_revision": document.source_revision or 0,
        "namespace": document.namespace.value if document.namespace else None,
        "project_id": str(document.project_id) if document.project_id else None,
        "conversation_id": (str(document.conversation_id) if document.conversation_id else None),
        "task_id": str(document.task_id) if document.task_id else None,
        "version_id": str(document.version_id) if document.version_id else None,
        "language": document.language,
        "normalized_text": document.normalized_text,
        "content_hash": document.content_hash,
        "source_cursor": document.source_cursor,
        "projection_generation": document.projection_generation,
        "updated_at": document.updated_at,
    }


def _fts_rowid_for_document(
    connection: Connection,
    *,
    tenant_id: str,
    document: MemorySearchDocument,
) -> int:
    source_revision = document.source_revision or 0
    existing = connection.execute(
        select(memory_search_documents.c.fts_rowid).where(
            memory_search_documents.c.tenant_id == tenant_id,
            memory_search_documents.c.projection_generation == document.projection_generation,
            memory_search_documents.c.source_kind == document.source_kind.value,
            memory_search_documents.c.source_id == str(document.source_id),
            memory_search_documents.c.source_revision == source_revision,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return int(existing)
    identity = "\0".join(
        (
            tenant_id,
            str(document.projection_generation),
            document.source_kind.value,
            str(document.source_id),
            str(source_revision),
        )
    ).encode("utf-8")
    candidate = int.from_bytes(hashlib.sha256(identity).digest()[:8], "big") & ((1 << 63) - 1)
    candidate = candidate or 1
    collision = connection.execute(
        select(
            memory_search_documents.c.tenant_id,
            memory_search_documents.c.source_kind,
            memory_search_documents.c.source_id,
        ).where(memory_search_documents.c.fts_rowid == candidate)
    ).first()
    if collision is not None:
        raise MemoryConflictError("stable FTS row identity collision")
    return candidate


def _insert_for(connection: Connection, table: Any):
    return (
        postgresql_insert(table)
        if connection.dialect.name == "postgresql"
        else sqlite_insert(table)
    )


def _require_projection_available(connection: Connection) -> None:
    if connection.dialect.name == "sqlite" and not sqlite_fts_available(connection):
        raise MemoryProjectionStaleError("SQLite FTS5 projection is unavailable")


def _validate_dialect(bind: Engine | Connection) -> None:
    if bind.dialect.name not in {"postgresql", "sqlite"}:
        raise ValueError(f"unsupported search dialect: {bind.dialect.name}")


def _unavailable_health(
    *,
    generation: int,
    source_watermark_cursor: int,
) -> MemoryProjectionHealth:
    return MemoryProjectionHealth(
        generation=generation,
        state=ProjectionState.UNAVAILABLE,
        source_watermark_cursor=source_watermark_cursor,
        projected_watermark_cursor=0,
        last_error_code="MEMORY_PROJECTION_UNAVAILABLE",
        updated_at=datetime.now(UTC),
    )


def _datetime(value: datetime | str) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


__all__ = ["SqlAlchemyMemoryProjectionWriter", "SqlAlchemyMemorySearchIndex"]
