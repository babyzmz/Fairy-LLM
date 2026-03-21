from __future__ import annotations

from app.memory.memory_store import StructuredMemoryStore
from app.memory.memory_vector_store import VectorMemoryStore


class MemoryPruner:
    def __init__(self, store: StructuredMemoryStore, vector_store: VectorMemoryStore, *, max_semantic_items: int = 1200) -> None:
        self.store = store
        self.vector_store = vector_store
        self.max_semantic_items = max_semantic_items

    def prune(self) -> dict[str, int]:
        expired = self.store.delete_expired_memories()
        self.store.decay_structured_importance()
        self.vector_store.merge_duplicates()
        capped = self.vector_store.cap_collection_size(self.max_semantic_items)
        return {
            "expired_deleted": expired,
            "semantic_trimmed": capped,
        }
