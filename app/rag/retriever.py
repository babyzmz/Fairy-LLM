from __future__ import annotations

from typing import Any

from app.rag.rag_schema import RetrievedChunk
from app.rag.vector_store import BaseVectorStore


class RAGRetriever:
    def __init__(self, vector_store: BaseVectorStore) -> None:
        self.vector_store = vector_store

    def search(
        self,
        query: str,
        *,
        top_k: int = 4,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        return self.vector_store.similarity_search(query, top_k=top_k, filters=filters)
