from __future__ import annotations

from app.memory.memory_store import StructuredMemoryStore


class LegacyMemoryInspector:
    def __init__(self, store: StructuredMemoryStore) -> None:
        self.store = store

    def inspect(self) -> dict[str, int]:
        structured = len(self.store.list_memory_rows(limit=5000))
        tasks = len(self.store.list_task_memories(limit=5000))
        user_profile = len(self.store.list_profile("user_profile"))
        env_profile = len(self.store.list_profile("environment_profile"))
        return {
            "structured_or_project_items": structured,
            "task_items": tasks,
            "user_profile_items": user_profile,
            "environment_profile_items": env_profile,
        }
