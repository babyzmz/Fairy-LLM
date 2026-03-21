from __future__ import annotations

import json
import uuid
from typing import Any

from app.storage.db import AppDatabase, get_app_database


class MemoryRepo:
    def __init__(self, db: AppDatabase | None = None) -> None:
        self.db = db or get_app_database()

    def save_item(
        self,
        *,
        memory_type: str,
        title: str,
        content: str,
        importance: float = 0.5,
        decision_status: str = "confirmed",
        source_session_id: str | None = None,
        source_message_ids: list[str] | None = None,
        tags: list[str] | None = None,
        evidence_count: int = 1,
        canonical_key: str = "",
        merged_source_refs: list[str] | None = None,
        item_id: str | None = None,
    ) -> str:
        memory_id = item_id or f"mem_{uuid.uuid4().hex[:14]}"
        now = self.db.now()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_items (
                    id, memory_type, title, content, importance, source_session_id,
                    decision_status, source_message_ids_json, evidence_count, canonical_key, merged_source_refs_json,
                    created_at, updated_at, tags_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    memory_type = excluded.memory_type,
                    title = excluded.title,
                    content = excluded.content,
                    importance = excluded.importance,
                    source_session_id = excluded.source_session_id,
                    decision_status = excluded.decision_status,
                    source_message_ids_json = excluded.source_message_ids_json,
                    evidence_count = excluded.evidence_count,
                    canonical_key = excluded.canonical_key,
                    merged_source_refs_json = excluded.merged_source_refs_json,
                    updated_at = excluded.updated_at,
                    tags_json = excluded.tags_json
                """,
                (
                    memory_id,
                    memory_type,
                    title,
                    content,
                    float(importance),
                    source_session_id,
                    decision_status,
                    json.dumps(source_message_ids or [], ensure_ascii=False),
                    int(evidence_count),
                    canonical_key or None,
                    json.dumps(merged_source_refs or [], ensure_ascii=False),
                    now,
                    now,
                    json.dumps(tags or [], ensure_ascii=False),
                ),
            )
        return memory_id

    def find_by_canonical_key(self, memory_type: str, canonical_key: str) -> dict[str, Any] | None:
        if not canonical_key:
            return None
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_items WHERE memory_type = ? AND canonical_key = ? LIMIT 1",
                (memory_type, canonical_key),
            ).fetchone()
        return dict(row) if row else None

    def merge_into_existing(
        self,
        *,
        item_id: str,
        content: str,
        importance: float,
        source_session_id: str | None,
        source_message_ids: list[str] | None,
        merged_source_refs: list[str] | None,
        append_line: str = "",
    ) -> None:
        existing = self.get_item(item_id)
        if existing is None:
            return
        current_content = str(existing.get("content", "") or "")
        updated_content = current_content
        if append_line and append_line not in current_content:
            updated_content = (current_content.rstrip() + "\n" + append_line.strip()).strip()
        current_sources = self._load_json_list(existing.get("source_message_ids_json"))
        current_sources.extend(source_message_ids or [])
        current_sources = list(dict.fromkeys(current_sources))
        current_refs = self._load_json_list(existing.get("merged_source_refs_json"))
        current_refs.extend(merged_source_refs or [])
        current_refs = list(dict.fromkeys(current_refs))
        current_evidence = int(existing.get("evidence_count", 1) or 1) + 1
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE memory_items
                SET content = ?, importance = MAX(importance, ?), source_session_id = COALESCE(?, source_session_id),
                    source_message_ids_json = ?, evidence_count = ?, merged_source_refs_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    updated_content,
                    float(importance),
                    source_session_id,
                    json.dumps(current_sources, ensure_ascii=False),
                    current_evidence,
                    json.dumps(current_refs, ensure_ascii=False),
                    self.db.now(),
                    item_id,
                ),
            )

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM memory_items WHERE id = ? LIMIT 1", (item_id,)).fetchone()
        return dict(row) if row else None

    def list_recent(self, *, memory_type: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        sql = "SELECT * FROM memory_items"
        params: list[Any] = []
        if memory_type:
            sql += " WHERE memory_type = ?"
            params.append(memory_type)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(int(limit))
        with self.db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def list_pending_decisions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_items
                WHERE memory_type = 'decision_card' AND decision_status = 'pending'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_decision_status(self, item_id: str, status: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE memory_items SET decision_status = ?, updated_at = ? WHERE id = ?",
                (status, self.db.now(), item_id),
            )

    def count_by_type_for_session(self, *, session_id: str, memory_type: str) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(1) AS count FROM memory_items WHERE source_session_id = ? AND memory_type = ?",
                (session_id, memory_type),
            ).fetchone()
        return int(row["count"]) if row else 0

    def _load_json_list(self, value: Any) -> list[str]:
        try:
            data = json.loads(str(value or "[]"))
        except Exception:
            return []
        return [str(item) for item in data if str(item).strip()]
