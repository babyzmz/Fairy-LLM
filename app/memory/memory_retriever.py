from __future__ import annotations

from app.memory.memory_injection_policy import MemoryInjectionPolicy
from app.memory.memory_schema import MemoryRetrievalBundle
from app.memory.memory_store import StructuredMemoryStore
from app.memory.memory_vector_store import VectorMemoryStore
from app.memory.relevance_scorer import find_relevant_memories


class MemoryRetriever:
    def __init__(self, store: StructuredMemoryStore, vector_store: VectorMemoryStore) -> None:
        self.store = store
        self.vector_store = vector_store

    def retrieve(
        self,
        *,
        query: str,
        project: str,
        task_id: str | None,
        task_category: str,
        policy: MemoryInjectionPolicy,
    ) -> MemoryRetrievalBundle:
        bundle = MemoryRetrievalBundle()
        if policy.include_profile:
            bundle.profile = self.store.list_profile("user_profile")[:6]
            bundle.environment = self.store.list_profile("environment_profile")[:6]
        if policy.include_project and project:
            bundle.project = self.store.query_project_memory(project, limit=6)
        if policy.include_task and task_id:
            task_row = self.store.get_task_memory(task_id)
            if task_row is not None:
                bundle.task = [task_row]
        if policy.include_semantic:
            semantic_scope = "web_research" if task_category == "web_research" else project or task_category
            candidates = self.vector_store.search(query, scope=semantic_scope, limit=12)
            if not candidates:
                candidates = self.vector_store.search(query, limit=12)
            bundle.semantic = find_relevant_memories(query, candidates, top_k=5)
        return bundle
