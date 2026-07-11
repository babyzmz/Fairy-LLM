from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.engine import Connection, Engine, RowMapping

from fairy_core.documents.models import (
    DocumentChunk,
    DocumentContext,
    DocumentRevision,
    DocumentStatus,
    DocumentVisibility,
    ManagedDocument,
)
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.models import ScopeContract
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.storage.schema import (
    document_chunks,
    document_revisions,
    documents,
)


class DocumentProjectionConflictError(RuntimeError):
    pass


class SqlAlchemyDocumentRepository:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str) -> None:
        _validate_dialect(bind)
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._session = SqlAlchemySession(bind)

    def add(
        self,
        document: ManagedDocument,
        revision: DocumentRevision,
        chunks: tuple[DocumentChunk, ...],
    ) -> None:
        if revision.document_id != document.id:
            raise ValueError("document revision does not belong to the document")
        if revision.revision != document.current_revision:
            raise ValueError("document revision is not current")
        if revision.content_hash != document.content_hash:
            raise ValueError("document revision hash does not match the document")
        if len(chunks) != revision.chunk_count:
            raise ValueError("document chunk count does not match the revision")
        if tuple(chunk.ordinal for chunk in chunks) != tuple(range(len(chunks))):
            raise ValueError("document chunk ordinals must be contiguous from zero")
        with self._session.write() as connection:
            connection.execute(insert(documents).values(**self._document_values(document)))
            connection.execute(insert(document_revisions).values(**self._revision_values(revision)))
            for chunk in chunks:
                if (
                    chunk.document_id != document.id
                    or chunk.revision != revision.revision
                    or chunk.revision_hash != revision.content_hash
                ):
                    raise ValueError("document chunk provenance does not match the revision")
                connection.execute(
                    insert(document_chunks).values(
                        **self._chunk_values(
                            chunk,
                            fts_rowid=self._fts_rowid(connection, chunk),
                        )
                    )
                )

    def get_context(
        self,
        document_id: UUID,
        *,
        include_deleted: bool = False,
    ) -> DocumentContext | None:
        predicates = [
            documents.c.tenant_id == self._tenant_id,
            documents.c.id == str(document_id),
        ]
        if not include_deleted:
            predicates.append(documents.c.status == DocumentStatus.ACTIVE.value)
        with self._session.read() as connection:
            row = (
                connection.execute(self._context_statement().where(*predicates)).mappings().first()
            )
        return self._context_from_row(row) if row is not None else None

    def find_by_idempotency_key(self, idempotency_key: str) -> DocumentContext | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    self._context_statement().where(
                        documents.c.tenant_id == self._tenant_id,
                        documents.c.idempotency_key == idempotency_key,
                    )
                )
                .mappings()
                .first()
            )
        return self._context_from_row(row) if row is not None else None

    def list_for_scope(
        self,
        scope: ScopeContract,
        *,
        limit: int,
    ) -> tuple[DocumentContext, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("document list limit must be between 1 and 100")
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    self._context_statement()
                    .where(
                        documents.c.tenant_id == self._tenant_id,
                        documents.c.status == DocumentStatus.ACTIVE.value,
                        _visible_to_scope(scope),
                    )
                    .order_by(documents.c.created_at.desc(), documents.c.id)
                    .limit(limit)
                )
                .mappings()
                .all()
            )
        return tuple(self._context_from_row(row) for row in rows)

    def update_status(
        self,
        document: ManagedDocument,
        *,
        expected_status: DocumentStatus,
    ) -> None:
        with self._session.write() as connection:
            result = connection.execute(
                update(documents)
                .where(
                    documents.c.tenant_id == self._tenant_id,
                    documents.c.id == str(document.id),
                    documents.c.project_id
                    == (str(document.project_id) if document.project_id else None),
                    documents.c.conversation_id == str(document.conversation_id),
                    documents.c.source_task_id == str(document.source_task_id),
                    documents.c.content_hash == document.content_hash,
                    documents.c.current_revision == document.current_revision,
                    documents.c.status == expected_status.value,
                )
                .values(
                    status=document.status.value,
                    updated_at=document.updated_at,
                )
            )
        if result.rowcount != 1:
            raise InvalidTransitionError("Document changed concurrently")

    @staticmethod
    def _context_statement():
        return select(documents, document_revisions).join(
            document_revisions,
            and_(
                document_revisions.c.tenant_id == documents.c.tenant_id,
                document_revisions.c.document_id == documents.c.id,
                document_revisions.c.revision == documents.c.current_revision,
                document_revisions.c.content_hash == documents.c.content_hash,
            ),
        )

    def _fts_rowid(self, connection: Connection, chunk: DocumentChunk) -> int:
        identity = f"{self._tenant_id}\0{chunk.id}".encode()
        candidate = int.from_bytes(hashlib.sha256(identity).digest()[:8], "big")
        candidate &= (1 << 63) - 1
        candidate = candidate or 1
        collision = connection.execute(
            select(document_chunks.c.tenant_id, document_chunks.c.id).where(
                document_chunks.c.fts_rowid == candidate
            )
        ).first()
        if collision is not None and collision != (self._tenant_id, str(chunk.id)):
            raise DocumentProjectionConflictError("stable document FTS row identity collision")
        return candidate

    def _document_values(self, document: ManagedDocument) -> dict[str, Any]:
        return {
            "tenant_id": self._tenant_id,
            "id": str(document.id),
            "project_id": str(document.project_id) if document.project_id else None,
            "conversation_id": str(document.conversation_id),
            "source_task_id": str(document.source_task_id),
            "version_id": str(document.version_id) if document.version_id else None,
            "filename": document.filename,
            "media_type": document.media_type,
            "byte_length": document.byte_length,
            "content_hash": document.content_hash,
            "storage_location": document.storage_location,
            "current_revision": document.current_revision,
            "visibility": document.visibility.value,
            "status": document.status.value,
            "idempotency_key": document.idempotency_key,
            "created_at": document.created_at,
            "updated_at": document.updated_at,
        }

    def _revision_values(self, revision: DocumentRevision) -> dict[str, Any]:
        return {
            "tenant_id": self._tenant_id,
            "document_id": str(revision.document_id),
            "revision": revision.revision,
            "content_hash": revision.content_hash,
            "byte_length": revision.byte_length,
            "media_type": revision.media_type,
            "parser": revision.parser,
            "parser_version": revision.parser_version,
            "section_count": revision.section_count,
            "chunk_count": revision.chunk_count,
            "created_at": revision.created_at,
        }

    def _chunk_values(self, chunk: DocumentChunk, *, fts_rowid: int) -> dict[str, Any]:
        return {
            "tenant_id": self._tenant_id,
            "id": str(chunk.id),
            "fts_rowid": fts_rowid,
            "document_id": str(chunk.document_id),
            "revision": chunk.revision,
            "revision_hash": chunk.revision_hash,
            "ordinal": chunk.ordinal,
            "section_ordinal": chunk.section_ordinal,
            "locator": dict(chunk.locator),
            "normalized_text": chunk.text,
            "content_hash": chunk.content_hash,
            "token_count": chunk.token_count,
            "updated_at": chunk.updated_at,
        }

    @staticmethod
    def _context_from_row(row: RowMapping) -> DocumentContext:
        document = ManagedDocument.restore(
            id=UUID(row[documents.c.id]),
            project_id=_uuid(row[documents.c.project_id]),
            conversation_id=UUID(row[documents.c.conversation_id]),
            source_task_id=UUID(row[documents.c.source_task_id]),
            version_id=_uuid(row[documents.c.version_id]),
            filename=row[documents.c.filename],
            media_type=row[documents.c.media_type],
            byte_length=int(row[documents.c.byte_length]),
            content_hash=row[documents.c.content_hash],
            storage_location=row[documents.c.storage_location],
            current_revision=int(row[documents.c.current_revision]),
            visibility=DocumentVisibility(row[documents.c.visibility]),
            status=DocumentStatus(row[documents.c.status]),
            idempotency_key=row[documents.c.idempotency_key],
            created_at=_datetime(row[documents.c.created_at]),
            updated_at=_datetime(row[documents.c.updated_at]),
        )
        revision = DocumentRevision.restore(
            document_id=UUID(row[document_revisions.c.document_id]),
            revision=int(row[document_revisions.c.revision]),
            content_hash=row[document_revisions.c.content_hash],
            byte_length=int(row[document_revisions.c.byte_length]),
            media_type=row[document_revisions.c.media_type],
            parser=row[document_revisions.c.parser],
            parser_version=row[document_revisions.c.parser_version],
            section_count=int(row[document_revisions.c.section_count]),
            chunk_count=int(row[document_revisions.c.chunk_count]),
            created_at=_datetime(row[document_revisions.c.created_at]),
        )
        return DocumentContext(document=document, revision=revision)


def _visible_to_scope(scope: ScopeContract):
    clauses = [
        and_(
            documents.c.visibility == DocumentVisibility.CONVERSATION.value,
            documents.c.conversation_id == str(scope.conversation_id),
        )
    ]
    if scope.project_id is not None:
        clauses.append(
            and_(
                documents.c.visibility == DocumentVisibility.PROJECT.value,
                documents.c.project_id == str(scope.project_id),
            )
        )
    return or_(*clauses)


def _uuid(value: str | None) -> UUID | None:
    return UUID(value) if value else None


def _datetime(value: datetime | str) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _validate_dialect(bind: Engine | Connection) -> None:
    if bind.dialect.name not in {"postgresql", "sqlite"}:
        raise ValueError(f"unsupported document repository dialect: {bind.dialect.name}")


__all__ = [
    "DocumentProjectionConflictError",
    "SqlAlchemyDocumentRepository",
]
