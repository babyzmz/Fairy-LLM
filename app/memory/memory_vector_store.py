from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.memory.memory_schema import SemanticMemoryRecord
from app.memory.memory_store import StructuredMemoryStore


@dataclass(slots=True)
class HashEmbeddingModel:
    dimension: int = 192

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]+|[\u4e00-\u9fff]{1,4}|\d+(?:\.\d+)?", text.lower())
        if not tokens:
            return vector
        for token in tokens:
            bucket = hash(token) % self.dimension
            vector[bucket] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class VectorMemoryStore:
    def __init__(self, db_path: Path, *, embedding_model: HashEmbeddingModel | None = None) -> None:
        self.store = StructuredMemoryStore(db_path)
        self.embedding_model = embedding_model or HashEmbeddingModel()

    def add_experience(
        self,
        memory_id: str,
        *,
        scope: str,
        content: str,
        importance: float,
        confidence: float,
        tags: list[str],
        metadata: dict[str, Any],
    ) -> None:
        timestamp = self.store.now()
        embedding = self.embedding_model.embed(content)
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO experience_memory (id, scope, content, embedding, importance, confidence, tags, metadata, created_at, updated_at, last_accessed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    scope=excluded.scope,
                    content=excluded.content,
                    embedding=excluded.embedding,
                    importance=excluded.importance,
                    confidence=excluded.confidence,
                    tags=excluded.tags,
                    metadata=excluded.metadata,
                    updated_at=excluded.updated_at
                """,
                (
                    memory_id,
                    scope,
                    content,
                    json.dumps(embedding),
                    float(importance),
                    float(confidence),
                    json.dumps(tags, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False),
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )

    def search(self, query: str, *, scope: str | None = None, limit: int = 5) -> list[SemanticMemoryRecord]:
        query_embedding = self.embedding_model.embed(query)
        sql = "SELECT * FROM experience_memory"
        params: list[Any] = []
        if scope:
            sql += " WHERE scope=?"
            params.append(scope)
        sql += " ORDER BY updated_at DESC"
        with self.store.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        scored: list[SemanticMemoryRecord] = []
        for row in rows:
            item = dict(row)
            try:
                embedding = json.loads(item.get("embedding") or "[]")
            except Exception:
                continue
            score = self._cosine_similarity(query_embedding, embedding)
            if score <= 0.08:
                continue
            try:
                tags = json.loads(item.get("tags") or "[]")
            except Exception:
                tags = []
            try:
                metadata = json.loads(item.get("metadata") or "{}")
            except Exception:
                metadata = {}
            scored.append(
                SemanticMemoryRecord(
                    id=str(item.get("id", "")),
                    scope=str(item.get("scope", "")),
                    content=str(item.get("content", "")),
                    score=score,
                    importance=float(item.get("importance", 0.0) or 0.0),
                    confidence=float(item.get("confidence", 0.0) or 0.0),
                    tags=list(tags),
                    metadata=dict(metadata),
                )
            )
        scored.sort(key=lambda record: (record.score * 0.7 + record.importance * 0.3), reverse=True)
        top_hits = scored[:limit]
        if top_hits:
            with self.store.connection() as conn:
                now = self.store.now()
                conn.executemany(
                    "UPDATE experience_memory SET last_accessed_at=? WHERE id=?",
                    [(now, item.id) for item in top_hits],
                )
        return top_hits

    def merge_duplicates(self) -> None:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT content, scope, MIN(id) AS keep_id, GROUP_CONCAT(id) AS ids, COUNT(*) AS count FROM experience_memory GROUP BY content, scope HAVING COUNT(*) > 1"
            ).fetchall()
            for row in rows:
                ids = [item for item in str(row["ids"]).split(",") if item]
                keep_id = str(row["keep_id"])
                drop_ids = [item for item in ids if item != keep_id]
                if drop_ids:
                    conn.executemany("DELETE FROM experience_memory WHERE id=?", [(item,) for item in drop_ids])

    def cap_collection_size(self, max_items: int) -> int:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT id FROM experience_memory ORDER BY importance DESC, last_accessed_at DESC, updated_at DESC"
            ).fetchall()
            stale = rows[max_items:]
            if stale:
                conn.executemany("DELETE FROM experience_memory WHERE id=?", [(row["id"],) for row in stale])
        return max(0, len(stale))

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute("SELECT * FROM experience_memory ORDER BY updated_at DESC LIMIT ?", (int(limit),)).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["tags"] = json.loads(item.get("tags") or "[]")
            except Exception:
                item["tags"] = []
            try:
                item["metadata"] = json.loads(item.get("metadata") or "{}")
            except Exception:
                item["metadata"] = {}
            items.append(item)
        return items

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left)) or 1.0
        right_norm = math.sqrt(sum(b * b for b in right)) or 1.0
        return numerator / (left_norm * right_norm)
