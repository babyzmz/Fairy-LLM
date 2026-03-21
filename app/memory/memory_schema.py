from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS user_profile (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS environment_profile (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS project_memory (
        id TEXT PRIMARY KEY,
        project TEXT NOT NULL,
        module TEXT NOT NULL,
        summary TEXT NOT NULL,
        importance REAL NOT NULL,
        updated_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS task_memory (
        task_id TEXT PRIMARY KEY,
        goal TEXT NOT NULL,
        current_step TEXT NOT NULL,
        done_steps TEXT NOT NULL,
        blocked_reason TEXT NOT NULL,
        last_url TEXT NOT NULL,
        status TEXT NOT NULL,
        updated_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS structured_memory (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        scope TEXT NOT NULL,
        content TEXT NOT NULL,
        confidence REAL NOT NULL,
        importance REAL NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        expires_at DATETIME,
        tags TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS experience_memory (
        id TEXT PRIMARY KEY,
        scope TEXT NOT NULL,
        content TEXT NOT NULL,
        embedding TEXT NOT NULL,
        importance REAL NOT NULL,
        confidence REAL NOT NULL,
        tags TEXT NOT NULL,
        metadata TEXT NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        last_accessed_at DATETIME NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_memory_project ON project_memory(project, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_structured_memory_scope_type ON structured_memory(scope, type, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_task_memory_status ON task_memory(status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_experience_memory_scope ON experience_memory(scope, updated_at DESC)",
)


@dataclass(slots=True)
class MemoryCandidate:
    type: str
    scope: str
    content: str
    confidence: float = 0.6
    importance: float = 0.5
    tags: list[str] = field(default_factory=list)
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SemanticMemoryRecord:
    id: str
    scope: str
    content: str
    score: float
    importance: float
    confidence: float
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryRetrievalBundle:
    profile: list[dict[str, Any]] = field(default_factory=list)
    environment: list[dict[str, Any]] = field(default_factory=list)
    project: list[dict[str, Any]] = field(default_factory=list)
    task: list[dict[str, Any]] = field(default_factory=list)
    semantic: list[SemanticMemoryRecord] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "profile": len(self.profile) + len(self.environment),
            "project": len(self.project),
            "task": len(self.task),
            "semantic": len(self.semantic),
        }
