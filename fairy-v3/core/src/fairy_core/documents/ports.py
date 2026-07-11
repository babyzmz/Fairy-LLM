from __future__ import annotations

from typing import Protocol
from uuid import UUID

from fairy_core.documents.models import (
    DocumentChunk,
    DocumentContext,
    DocumentRevision,
    DocumentSearchHit,
    DocumentStatus,
    ExtractedDocument,
    ManagedDocument,
    StoredDocumentBlob,
)
from fairy_core.domain.models import ScopeContract


class DocumentParser(Protocol):
    def parse(
        self,
        *,
        filename: str,
        media_type: str,
        content: bytes,
    ) -> ExtractedDocument: ...


class DocumentBlobStore(Protocol):
    def put(
        self,
        *,
        content_hash: str,
        content: bytes,
        media_type: str,
    ) -> StoredDocumentBlob: ...

    def read(self, blob: StoredDocumentBlob) -> bytes: ...


class DocumentRepository(Protocol):
    def add(
        self,
        document: ManagedDocument,
        revision: DocumentRevision,
        chunks: tuple[DocumentChunk, ...],
    ) -> None: ...

    def get_context(
        self,
        document_id: UUID,
        *,
        include_deleted: bool = False,
    ) -> DocumentContext | None: ...

    def find_by_idempotency_key(self, idempotency_key: str) -> DocumentContext | None: ...

    def list_for_scope(
        self,
        scope: ScopeContract,
        *,
        limit: int,
    ) -> tuple[DocumentContext, ...]: ...

    def update_status(
        self,
        document: ManagedDocument,
        *,
        expected_status: DocumentStatus,
    ) -> None: ...


class DocumentSearchIndex(Protocol):
    def search(
        self,
        *,
        scope: ScopeContract,
        query: str,
        limit: int,
    ) -> tuple[DocumentSearchHit, ...]: ...


__all__ = [
    "DocumentBlobStore",
    "DocumentParser",
    "DocumentRepository",
    "DocumentSearchIndex",
]
