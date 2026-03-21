from __future__ import annotations

import json
import logging
import math
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.config import BASE_DIR
from app.rag.embedding_service import BaseEmbeddingService, EmbeddingRuntime
from app.rag.rag_schema import ChunkRecord, RAGSettings, RetrievedChunk
from app.storage.db import AppDatabase, get_app_database


logger = logging.getLogger(__name__)


def _inject_vendor_site_packages() -> None:
    vendor = BASE_DIR / ".vendor" / "site-packages"
    vendor_path = str(vendor)
    if vendor.exists() and vendor_path not in sys.path:
        sys.path.insert(0, vendor_path)


class BaseVectorStore(ABC):
    def __init__(self, embedding_runtime: EmbeddingRuntime) -> None:
        self.embedding_runtime = embedding_runtime
        self.embedding_fingerprint = embedding_runtime.fingerprint
        self.embedding_provider = embedding_runtime.provider_name
        self.embedding_model_id = embedding_runtime.model_id
        self.embedding_dim = embedding_runtime.dimension
        self.collection_name = embedding_runtime.collection_name
        self.backend_name = "base"

    @abstractmethod
    def upsert_chunks(self, chunks: list[ChunkRecord], *, update_chunk_metadata: bool = True) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_by_source_ref_id(self, source_ref_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def similarity_search(
        self,
        query: str,
        *,
        top_k: int = 4,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        raise NotImplementedError

    @abstractmethod
    def count_vectors(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def fetch_chunk_ids(self, chunk_ids: list[str]) -> set[str]:
        raise NotImplementedError


class SQLiteVectorStore(BaseVectorStore):
    def __init__(self, db: AppDatabase, embedding_service: BaseEmbeddingService, embedding_runtime: EmbeddingRuntime) -> None:
        super().__init__(embedding_runtime)
        self.db = db
        self.embedding_service = embedding_service
        self.backend_name = "sqlite"
        logger.info(
            "embedding_collection_created backend=%s collection=%s fingerprint=%s",
            self.backend_name,
            self.collection_name,
            self.embedding_fingerprint,
        )

    def upsert_chunks(self, chunks: list[ChunkRecord], *, update_chunk_metadata: bool = True) -> None:
        if not chunks:
            return
        embeddings = self.embedding_service.embed_texts([chunk.content for chunk in chunks])
        with self.db.connect() as conn:
            for chunk, embedding in zip(chunks, embeddings):
                metadata = {
                    "source_kind": chunk.metadata.source_kind,
                    "source_ref_id": chunk.metadata.source_ref_id,
                    "title": chunk.metadata.title,
                    "tags": chunk.metadata.tags,
                    "created_at": chunk.metadata.created_at,
                    "session_id": chunk.metadata.session_id,
                    "importance": chunk.metadata.importance,
                    "document_id": chunk.metadata.document_id,
                    "embedding_provider": self.embedding_provider,
                    "embedding_model_id": self.embedding_model_id,
                    "embedding_dim": len(embedding),
                    "embedding_fingerprint": self.embedding_fingerprint,
                }
                if update_chunk_metadata:
                    conn.execute(
                        "UPDATE document_chunks SET embedding_fingerprint = ?, metadata_json = ? WHERE id = ?",
                        (self.embedding_fingerprint, json.dumps(metadata, ensure_ascii=False), chunk.id),
                    )
                conn.execute(
                    """
                    INSERT INTO chunk_embeddings_store (
                        id, chunk_id, source_ref_id, embedding_fingerprint, embedding_provider, embedding_model_id,
                        embedding_dim, content, embedding_json, metadata_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_id, embedding_fingerprint) DO UPDATE SET
                        source_ref_id = excluded.source_ref_id,
                        embedding_fingerprint = excluded.embedding_fingerprint,
                        embedding_provider = excluded.embedding_provider,
                        embedding_model_id = excluded.embedding_model_id,
                        embedding_dim = excluded.embedding_dim,
                        content = excluded.content,
                        embedding_json = excluded.embedding_json,
                        metadata_json = excluded.metadata_json,
                        created_at = excluded.created_at
                    """,
                    (
                        f"{chunk.id}:{self.embedding_fingerprint}",
                        chunk.id,
                        chunk.metadata.source_ref_id,
                        self.embedding_fingerprint,
                        self.embedding_provider,
                        self.embedding_model_id,
                        len(embedding),
                        chunk.content,
                        json.dumps(embedding),
                        json.dumps(metadata, ensure_ascii=False),
                        self.db.now(),
                    ),
                )

    def delete_by_source_ref_id(self, source_ref_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM chunk_embeddings_store WHERE source_ref_id = ? AND embedding_fingerprint = ?",
                (source_ref_id, self.embedding_fingerprint),
            )

    def similarity_search(
        self,
        query: str,
        *,
        top_k: int = 4,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        query_embedding = self.embedding_service.embed_texts([query])[0]
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT e.chunk_id, e.content, e.embedding_json, e.metadata_json, e.embedding_fingerprint,
                       c.source_kind, c.source_ref_id
                FROM chunk_embeddings_store e
                JOIN document_chunks c ON c.id = e.chunk_id
                WHERE e.embedding_fingerprint = ?
                ORDER BY c.created_at DESC
                """,
                (self.embedding_fingerprint,),
            ).fetchall()

        hits: list[RetrievedChunk] = []
        for row in rows:
            try:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
                embedding = json.loads(str(row["embedding_json"] or "[]"))
            except Exception:
                continue
            if not self._matches_filters(metadata, filters or {}):
                continue
            score = self._cosine_similarity(query_embedding, embedding)
            if score <= 0.08:
                continue
            hits.append(
                RetrievedChunk(
                    chunk_id=str(row["chunk_id"]),
                    score=score,
                    content=str(row["content"]),
                    source_kind=str(metadata.get("source_kind") or row["source_kind"] or ""),
                    source_ref_id=str(metadata.get("source_ref_id") or row["source_ref_id"] or ""),
                    title=str(metadata.get("title", "")),
                    tags=list(metadata.get("tags") or []),
                    created_at=str(metadata.get("created_at", "")),
                    session_id=str(metadata.get("session_id", "")),
                    importance=float(metadata.get("importance", 0.0) or 0.0),
                    document_id=str(metadata.get("document_id", "")),
                    embedding_fingerprint=str(metadata.get("embedding_fingerprint", self.embedding_fingerprint)),
                )
            )
        hits.sort(key=lambda item: (item.score * 0.8 + item.importance * 0.2), reverse=True)
        return hits[:top_k]

    def count_vectors(self) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(1) AS count FROM chunk_embeddings_store WHERE embedding_fingerprint = ?",
                (self.embedding_fingerprint,),
            ).fetchone()
        return int(row["count"]) if row else 0

    def fetch_chunk_ids(self, chunk_ids: list[str]) -> set[str]:
        normalized = [str(item).strip() for item in chunk_ids if str(item).strip()]
        if not normalized:
            return set()
        placeholders = ",".join("?" for _ in normalized)
        params: list[Any] = [self.embedding_fingerprint, *normalized]
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT chunk_id FROM chunk_embeddings_store
                WHERE embedding_fingerprint = ? AND chunk_id IN ({placeholders})
                """,
                params,
            ).fetchall()
        return {str(row["chunk_id"]) for row in rows}

    def _matches_filters(self, metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
        for key, expected in filters.items():
            if expected in (None, "", [], {}):
                continue
            actual = metadata.get(key)
            if key == "tags":
                actual_tags = list(actual or [])
                required = list(expected if isinstance(expected, list) else [expected])
                if not any(tag in actual_tags for tag in required):
                    return False
                continue
            if actual != expected:
                return False
        return True

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left)) or 1.0
        right_norm = math.sqrt(sum(b * b for b in right)) or 1.0
        return numerator / (left_norm * right_norm)


class ChromaVectorStore(BaseVectorStore):
    def __init__(
        self,
        embedding_service: BaseEmbeddingService,
        embedding_runtime: EmbeddingRuntime,
        *,
        persist_dir: Path,
    ) -> None:
        _inject_vendor_site_packages()
        import chromadb

        super().__init__(embedding_runtime)
        self.backend_name = "chroma"
        persist_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_service = embedding_service
        self.client = chromadb.PersistentClient(path=str(persist_dir))
        self.collection = self.client.get_or_create_collection(name=self.collection_name)
        logger.info("chroma_initialized collection=%s path=%s", self.collection_name, persist_dir)
        logger.info(
            "embedding_collection_created backend=%s collection=%s fingerprint=%s",
            self.backend_name,
            self.collection_name,
            self.embedding_fingerprint,
        )

    def upsert_chunks(self, chunks: list[ChunkRecord], *, update_chunk_metadata: bool = True) -> None:
        if not chunks:
            return
        embeddings = self.embedding_service.embed_texts([chunk.content for chunk in chunks])
        if update_chunk_metadata:
            # Keep SQLite metadata and Chroma metadata behavior aligned when the active collection is updated.
            pass
        self.collection.upsert(
            ids=[chunk.id for chunk in chunks],
            documents=[chunk.content for chunk in chunks],
            embeddings=embeddings,
            metadatas=[
                {
                    "source_kind": chunk.metadata.source_kind,
                    "source_ref_id": chunk.metadata.source_ref_id,
                    "title": chunk.metadata.title,
                    "tags": ",".join(chunk.metadata.tags),
                    "created_at": chunk.metadata.created_at,
                    "session_id": chunk.metadata.session_id,
                    "importance": chunk.metadata.importance,
                    "document_id": chunk.metadata.document_id,
                    "embedding_provider": self.embedding_provider,
                    "embedding_model_id": self.embedding_model_id,
                    "embedding_dim": self.embedding_dim,
                    "embedding_fingerprint": self.embedding_fingerprint,
                }
                for chunk in chunks
            ],
        )

    def delete_by_source_ref_id(self, source_ref_id: str) -> None:
        self.collection.delete(where={"source_ref_id": source_ref_id})

    def similarity_search(
        self,
        query: str,
        *,
        top_k: int = 4,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        query_embedding = self.embedding_service.embed_texts([query])[0]
        where = {key: value for key, value in (filters or {}).items() if key != "tags" and value not in (None, "", [], {})}
        result = self.collection.query(query_embeddings=[query_embedding], n_results=top_k, where=where or None)
        hits: list[RetrievedChunk] = []
        ids = result.get("ids", [[]])[0]
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        for chunk_id, content, metadata, distance in zip(ids, docs, metas, distances):
            tags = [tag for tag in str(metadata.get("tags", "")).split(",") if tag]
            if filters and filters.get("tags"):
                required = list(filters["tags"] if isinstance(filters["tags"], list) else [filters["tags"]])
                if not any(tag in tags for tag in required):
                    continue
            hits.append(
                RetrievedChunk(
                    chunk_id=str(chunk_id),
                    score=max(0.0, 1.0 - float(distance or 0.0)),
                    content=str(content),
                    source_kind=str(metadata.get("source_kind", "")),
                    source_ref_id=str(metadata.get("source_ref_id", "")),
                    title=str(metadata.get("title", "")),
                    tags=tags,
                    created_at=str(metadata.get("created_at", "")),
                    session_id=str(metadata.get("session_id", "")),
                    importance=float(metadata.get("importance", 0.0) or 0.0),
                    document_id=str(metadata.get("document_id", "")),
                    embedding_fingerprint=str(metadata.get("embedding_fingerprint", self.embedding_fingerprint)),
                )
            )
        return hits

    def count_vectors(self) -> int:
        return int(self.collection.count())

    def fetch_chunk_ids(self, chunk_ids: list[str]) -> set[str]:
        normalized = [str(item).strip() for item in chunk_ids if str(item).strip()]
        if not normalized:
            return set()
        result = self.collection.get(ids=normalized)
        ids = result.get("ids", []) or []
        return {str(item) for item in ids if str(item).strip()}


def build_vector_store(
    *,
    settings: RAGSettings,
    embedding_runtime: EmbeddingRuntime,
    db: AppDatabase | None = None,
) -> BaseVectorStore:
    backend = (settings.vector_backend or "auto").strip().lower()
    persist_dir = Path(settings.chroma_persist_path.strip()) if settings.chroma_persist_path.strip() else BASE_DIR / "data" / "chroma"

    if backend in {"auto", "chroma"}:
        try:
            _inject_vendor_site_packages()
            import chromadb  # noqa: F401

            return ChromaVectorStore(
                embedding_runtime.service,
                embedding_runtime,
                persist_dir=persist_dir,
            )
        except Exception as exc:
            logger.warning("chroma_fallback_sqlite reason=%s", exc)
            if backend == "chroma":
                logger.info("chroma_initialized failed, using sqlite fallback")

    return SQLiteVectorStore(db or get_app_database(), embedding_runtime.service, embedding_runtime)
