from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any

from app.rag.chunker import TextChunker
from app.rag.vector_store import BaseVectorStore
from app.storage.repositories.document_repo import DocumentRepo
from app.storage.repositories.memory_repo import MemoryRepo


class IngestionPipeline:
    def __init__(
        self,
        *,
        memory_repo: MemoryRepo,
        document_repo: DocumentRepo,
        vector_store: BaseVectorStore,
        chunker: TextChunker | None = None,
    ) -> None:
        self.memory_repo = memory_repo
        self.document_repo = document_repo
        self.vector_store = vector_store
        self.chunker = chunker or TextChunker()

    def save_session_summary(
        self,
        *,
        title: str,
        content: str,
        session_id: str | None = None,
        tags: list[str] | None = None,
        importance: float = 0.65,
    ) -> str:
        item_id = self.memory_repo.save_item(
            memory_type="session_summary",
            title=title,
            content=content,
            importance=importance,
            source_session_id=session_id,
            tags=tags or ["session_summary"],
        )
        self._replace_chunks(
            source_kind="message_summary",
            source_ref_id=item_id,
            title=title,
            content=content,
            session_id=session_id or "",
            importance=importance,
            tags=tags or ["session_summary"],
        )
        return item_id

    def save_decision_card(
        self,
        *,
        title: str,
        content: str,
        session_id: str | None = None,
        source_message_ids: list[str] | None = None,
        tags: list[str] | None = None,
        importance: float = 0.88,
    ) -> str:
        item_id = self.memory_repo.save_item(
            memory_type="decision_card",
            title=title,
            content=content,
            importance=importance,
            source_session_id=session_id,
            source_message_ids=source_message_ids,
            tags=tags or ["decision_card"],
        )
        self._replace_chunks(
            source_kind="decision_card",
            source_ref_id=item_id,
            title=title,
            content=content,
            session_id=session_id or "",
            importance=importance,
            tags=tags or ["decision_card"],
        )
        return item_id

    def ingest_document(
        self,
        file_path: str | Path,
        *,
        title: str = "",
        source_type: str = "local_file",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        path = Path(file_path)
        content = self._read_document_text(path)
        checksum = hashlib.sha1(content.encode("utf-8")).hexdigest()
        document_id = self.document_repo.save_document(
            source_type=source_type,
            title=title or path.name,
            file_path=str(path),
            mime_type=mimetypes.guess_type(path.name)[0] or "text/plain",
            checksum=checksum,
            metadata=metadata or {},
        )
        self._replace_chunks(
            source_kind="document_chunk",
            source_ref_id=document_id,
            title=title or path.name,
            content=content,
            document_id=document_id,
            tags=list((metadata or {}).get("tags") or []),
            importance=float((metadata or {}).get("importance", 0.72) or 0.72),
        )
        return document_id

    def _replace_chunks(
        self,
        *,
        source_kind: str,
        source_ref_id: str,
        title: str,
        content: str,
        session_id: str = "",
        document_id: str = "",
        importance: float = 0.0,
        tags: list[str] | None = None,
    ) -> None:
        chunks = self.chunker.chunk_text(
            text=content,
            source_kind=source_kind,
            source_ref_id=source_ref_id,
            title=title,
            tags=tags or [],
            created_at="",
            session_id=session_id,
            importance=importance,
            document_id=document_id,
        )
        rows = [
            {
                "id": chunk.id,
                "document_id": document_id or None,
                "source_kind": chunk.metadata.source_kind,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "token_estimate": chunk.token_estimate,
                "metadata": {
                    "source_kind": chunk.metadata.source_kind,
                    "source_ref_id": chunk.metadata.source_ref_id,
                    "title": chunk.metadata.title,
                    "tags": chunk.metadata.tags,
                    "created_at": chunk.metadata.created_at,
                    "session_id": chunk.metadata.session_id,
                    "importance": chunk.metadata.importance,
                    "document_id": chunk.metadata.document_id,
                    "embedding_provider": getattr(self.vector_store, "embedding_provider", ""),
                    "embedding_model_id": getattr(self.vector_store, "embedding_model_id", ""),
                    "embedding_dim": getattr(self.vector_store, "embedding_dim", 0),
                    "embedding_fingerprint": getattr(self.vector_store, "embedding_fingerprint", ""),
                },
                "embedding_fingerprint": getattr(self.vector_store, "embedding_fingerprint", ""),
            }
            for chunk in chunks
        ]
        self.document_repo.replace_chunks(source_ref_id=source_ref_id, chunks=rows)
        self.vector_store.delete_by_source_ref_id(source_ref_id)
        self.vector_store.upsert_chunks(chunks)

    def _read_document_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return path.read_text(encoding="utf-8", errors="ignore")
