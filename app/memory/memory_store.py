from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.memory.memory_schema import SCHEMA_SQL


class StructuredMemoryStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self.connection() as conn:
            for statement in SCHEMA_SQL:
                conn.execute(statement)

    @contextmanager
    def connection(self) -> Iterable[sqlite3.Connection]:
        with self._lock:
            conn = self._connect()
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def upsert_profile(self, table: str, key: str, value: str) -> None:
        timestamp = self.now()
        with self.connection() as conn:
            conn.execute(
                f"INSERT INTO {table} (key, value, updated_at) VALUES (?, ?, ?) "
                f"ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, timestamp),
            )

    def list_profile(self, table: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(f"SELECT key, value, updated_at FROM {table} ORDER BY key").fetchall()
        return [dict(row) for row in rows]

    def upsert_project_memory(self, memory_id: str, project: str, module: str, summary: str, importance: float) -> None:
        timestamp = self.now()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO project_memory (id, project, module, summary, importance, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project=excluded.project,
                    module=excluded.module,
                    summary=excluded.summary,
                    importance=excluded.importance,
                    updated_at=excluded.updated_at
                """,
                (memory_id, project, module, summary, float(importance), timestamp),
            )

    def query_project_memory(self, project: str, limit: int) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM project_memory WHERE project=? ORDER BY importance DESC, updated_at DESC LIMIT ?",
                (project, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_structured_memory(
        self,
        memory_id: str,
        memory_type: str,
        scope: str,
        content: str,
        confidence: float,
        importance: float,
        tags: list[str],
        expires_at: str | None = None,
    ) -> None:
        timestamp = self.now()
        tags_json = json.dumps(tags, ensure_ascii=False)
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO structured_memory (id, type, scope, content, confidence, importance, created_at, updated_at, expires_at, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    type=excluded.type,
                    scope=excluded.scope,
                    content=excluded.content,
                    confidence=excluded.confidence,
                    importance=excluded.importance,
                    updated_at=excluded.updated_at,
                    expires_at=excluded.expires_at,
                    tags=excluded.tags
                """,
                (memory_id, memory_type, scope, content, float(confidence), float(importance), timestamp, timestamp, expires_at, tags_json),
            )

    def find_structured_by_content(self, memory_type: str, scope: str, content: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM structured_memory WHERE type=? AND scope=? AND content=? LIMIT 1",
                (memory_type, scope, content),
            ).fetchone()
        return dict(row) if row else None

    def query_structured_memory(self, *, scopes: list[str] | None = None, types: list[str] | None = None, limit: int = 12) -> list[dict[str, Any]]:
        query = "SELECT * FROM structured_memory WHERE 1=1"
        params: list[Any] = []
        if scopes:
            query += f" AND scope IN ({','.join('?' for _ in scopes)})"
            params.extend(scopes)
        if types:
            query += f" AND type IN ({','.join('?' for _ in types)})"
            params.extend(types)
        query += " ORDER BY importance DESC, updated_at DESC LIMIT ?"
        params.append(int(limit))
        with self.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["tags"] = json.loads(item.get("tags") or "[]")
            except Exception:
                item["tags"] = []
            result.append(item)
        return result

    def create_or_update_task_memory(
        self,
        task_id: str,
        goal: str,
        *,
        current_step: str = "",
        done_steps: list[str] | None = None,
        blocked_reason: str = "",
        last_url: str = "",
        status: str = "running",
    ) -> None:
        timestamp = self.now()
        steps_json = json.dumps(done_steps or [], ensure_ascii=False)
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO task_memory (task_id, goal, current_step, done_steps, blocked_reason, last_url, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    goal=excluded.goal,
                    current_step=excluded.current_step,
                    done_steps=excluded.done_steps,
                    blocked_reason=excluded.blocked_reason,
                    last_url=excluded.last_url,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (task_id, goal, current_step, steps_json, blocked_reason, last_url, status, timestamp),
            )

    def get_task_memory(self, task_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM task_memory WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        try:
            item["done_steps"] = json.loads(item.get("done_steps") or "[]")
        except Exception:
            item["done_steps"] = []
        return item

    def list_task_memories(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM task_memory ORDER BY updated_at DESC LIMIT ?", (int(limit),)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["done_steps"] = json.loads(item.get("done_steps") or "[]")
            except Exception:
                item["done_steps"] = []
            items.append(item)
        return items

    def list_memory_rows(self, limit: int = 50) -> list[dict[str, Any]]:
        query = (
            "SELECT id, type, scope, content, confidence, importance, updated_at, tags FROM structured_memory "
            "UNION ALL "
            "SELECT id, 'project_state' AS type, project AS scope, summary AS content, 1.0 AS confidence, importance, updated_at, '[]' AS tags FROM project_memory "
            "ORDER BY updated_at DESC LIMIT ?"
        )
        with self.connection() as conn:
            rows = conn.execute(query, (int(limit),)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["tags"] = json.loads(item.get("tags") or "[]")
            except Exception:
                item["tags"] = []
            items.append(item)
        return items

    def show_memory(self, memory_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM structured_memory WHERE id=?", (memory_id,)).fetchone()
            if row is not None:
                item = dict(row)
                try:
                    item["tags"] = json.loads(item.get("tags") or "[]")
                except Exception:
                    item["tags"] = []
                return item
            row = conn.execute("SELECT * FROM project_memory WHERE id=?", (memory_id,)).fetchone()
            if row is not None:
                return dict(row)
            row = conn.execute("SELECT * FROM task_memory WHERE task_id=?", (memory_id,)).fetchone()
            if row is not None:
                item = dict(row)
                try:
                    item["done_steps"] = json.loads(item.get("done_steps") or "[]")
                except Exception:
                    item["done_steps"] = []
                return item
        return None

    def delete_memory(self, memory_id: str) -> bool:
        with self.connection() as conn:
            deleted = 0
            for query in (
                ("DELETE FROM structured_memory WHERE id=?", memory_id),
                ("DELETE FROM project_memory WHERE id=?", memory_id),
                ("DELETE FROM task_memory WHERE task_id=?", memory_id),
                ("DELETE FROM experience_memory WHERE id=?", memory_id),
            ):
                cur = conn.execute(query[0], (query[1],))
                deleted += cur.rowcount
        return deleted > 0

    def delete_expired_memories(self) -> int:
        timestamp = self.now()
        with self.connection() as conn:
            cur = conn.execute(
                "DELETE FROM structured_memory WHERE expires_at IS NOT NULL AND expires_at <> '' AND expires_at < ?",
                (timestamp,),
            )
        return int(cur.rowcount)

    def decay_structured_importance(self, factor: float = 0.98) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE structured_memory SET importance = MAX(0.05, importance * ?) WHERE importance > 0.05",
                (float(factor),),
            )
            conn.execute(
                "UPDATE project_memory SET importance = MAX(0.05, importance * ?) WHERE importance > 0.05",
                (float(factor),),
            )
