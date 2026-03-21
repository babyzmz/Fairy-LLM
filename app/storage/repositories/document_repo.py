from __future__ import annotations

import json
import uuid
from typing import Any

from app.storage.db import AppDatabase, get_app_database


class DocumentRepo:
    def __init__(self, db: AppDatabase | None = None) -> None:
        self.db = db or get_app_database()

    def save_document(
        self,
        *,
        source_type: str,
        title: str,
        file_path: str,
        mime_type: str,
        checksum: str,
        metadata: dict[str, Any] | None = None,
        document_id: str | None = None,
    ) -> str:
        doc_id = document_id
        with self.db.connect() as conn:
            if doc_id is None and checksum:
                existing = conn.execute(
                    "SELECT id FROM documents WHERE checksum = ? LIMIT 1",
                    (checksum,),
                ).fetchone()
                if existing is not None:
                    doc_id = str(existing["id"])
        doc_id = doc_id or f"doc_{uuid.uuid4().hex[:14]}"
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO documents (id, source_type, title, file_path, mime_type, checksum, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_type = excluded.source_type,
                    title = excluded.title,
                    file_path = excluded.file_path,
                    mime_type = excluded.mime_type,
                    checksum = excluded.checksum,
                    metadata_json = excluded.metadata_json
                """,
                (
                    doc_id,
                    source_type,
                    title,
                    file_path,
                    mime_type,
                    checksum,
                    self.db.now(),
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
        return doc_id

    def replace_chunks(self, *, source_ref_id: str, chunks: list[dict[str, Any]]) -> list[str]:
        with self.db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM document_chunks WHERE source_ref_id = ?",
                (source_ref_id,),
            ).fetchall()
            existing_ids = [str(row["id"]) for row in existing]
            if existing_ids:
                conn.executemany("DELETE FROM chunk_embeddings_store WHERE chunk_id = ?", [(chunk_id,) for chunk_id in existing_ids])
                conn.executemany("DELETE FROM document_chunks WHERE id = ?", [(chunk_id,) for chunk_id in existing_ids])

            created_ids: list[str] = []
            for chunk in chunks:
                chunk_id = chunk.get("id") or f"chunk_{uuid.uuid4().hex[:16]}"
                created_ids.append(str(chunk_id))
                conn.execute(
                    """
                    INSERT INTO document_chunks (
                        id, document_id, source_kind, source_ref_id, embedding_fingerprint, chunk_index,
                        content, token_estimate, created_at, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        chunk.get("document_id"),
                        chunk["source_kind"],
                        source_ref_id,
                        chunk.get("embedding_fingerprint", ""),
                        int(chunk.get("chunk_index", 0)),
                        chunk["content"],
                        int(chunk.get("token_estimate", 0)),
                        self.db.now(),
                        json.dumps(chunk.get("metadata", {}), ensure_ascii=False),
                    ),
                )
        return created_ids

    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM document_chunks WHERE id = ? LIMIT 1", (chunk_id,)).fetchone()
        return dict(row) if row else None

    def list_chunks(
        self,
        *,
        source_kind: str | None = None,
        document_id: str | None = None,
        source_ref_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM document_chunks WHERE 1=1"
        params: list[Any] = []
        if source_kind:
            sql += " AND source_kind = ?"
            params.append(source_kind)
        if document_id:
            sql += " AND document_id = ?"
            params.append(document_id)
        if source_ref_id:
            sql += " AND source_ref_id = ?"
            params.append(source_ref_id)
        sql += " ORDER BY created_at DESC, chunk_index ASC LIMIT ?"
        params.append(int(limit))
        with self.db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def list_chunks_by_ids(self, chunk_ids: list[str]) -> list[dict[str, Any]]:
        ids = [str(chunk_id).strip() for chunk_id in chunk_ids if str(chunk_id).strip()]
        if not ids:
            return []
        placeholders = ", ".join("?" for _ in ids)
        with self.db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM document_chunks WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        mapping = {str(row["id"]): dict(row) for row in rows}
        return [mapping[chunk_id] for chunk_id in ids if chunk_id in mapping]

    def count_reindexable_chunks(self, *, source_kind: str | None = None, document_id: str | None = None, source_ref_id: str | None = None) -> int:
        sql = "SELECT COUNT(1) AS count FROM document_chunks WHERE TRIM(content) != ''"
        params: list[Any] = []
        if source_kind:
            sql += " AND source_kind = ?"
            params.append(source_kind)
        if document_id:
            sql += " AND document_id = ?"
            params.append(document_id)
        if source_ref_id:
            sql += " AND source_ref_id = ?"
            params.append(source_ref_id)
        with self.db.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["count"]) if row else 0

    def list_reindexable_chunks(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        source_kind: str | None = None,
        document_id: str | None = None,
        source_ref_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM document_chunks WHERE TRIM(content) != ''"
        params: list[Any] = []
        if source_kind:
            sql += " AND source_kind = ?"
            params.append(source_kind)
        if document_id:
            sql += " AND document_id = ?"
            params.append(document_id)
        if source_ref_id:
            sql += " AND source_ref_id = ?"
            params.append(source_ref_id)
        sql += " ORDER BY created_at ASC, chunk_index ASC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        with self.db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
