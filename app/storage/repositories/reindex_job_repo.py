from __future__ import annotations

import json
import uuid
from typing import Any

from app.storage.db import AppDatabase, get_app_database


class ReindexJobRepo:
    def __init__(self, db: AppDatabase | None = None) -> None:
        self.db = db or get_app_database()

    def create_job(
        self,
        *,
        source_fingerprint: str,
        target_fingerprint: str,
        target_provider: str,
        target_model: str,
        scope_type: str,
        scope_ref: str = "",
        total_chunks: int = 0,
        status: str = "queued",
        promotion_status: str = "not_ready",
        completion_reason: str = "",
        backend_type: str = "",
        output_collection_name: str = "",
        output_vector_dim: int = 0,
        output_embedding_fingerprint: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job_id = f"reidx_{uuid.uuid4().hex[:14]}"
        now = self.db.now()
        payload = {
            "scope_type": scope_type,
            "scope_ref": scope_ref,
            **(metadata or {}),
        }
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO reindex_jobs (
                    job_id, status, promotion_status, completion_reason,
                    source_fingerprint, target_fingerprint, target_provider, target_model,
                    total_chunks, processed_chunks, succeeded_chunks, failed_chunks, skipped_chunks,
                    warning_count, created_at, started_at, finished_at, cancel_requested_at, cancelled_at,
                    cancel_reason, promoted_at, last_progress_at, last_error, backend_type,
                    output_collection_name, output_vector_dim, output_embedding_fingerprint,
                    health_check_passed, health_check_summary_json, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, ?, NULL, NULL, NULL, NULL, '', NULL, ?, '', ?, ?, ?, ?, 0, '{}', ?)
                """,
                (
                    job_id,
                    status,
                    promotion_status,
                    completion_reason,
                    source_fingerprint,
                    target_fingerprint,
                    target_provider,
                    target_model,
                    int(total_chunks),
                    now,
                    now,
                    backend_type,
                    output_collection_name,
                    int(output_vector_dim),
                    output_embedding_fingerprint,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
        return self.get_job(job_id) or {}

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM reindex_jobs WHERE job_id = ? LIMIT 1", (job_id,)).fetchone()
        return self._normalize(row) if row else None

    def list_jobs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reindex_jobs ORDER BY created_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._normalize(row) for row in rows]

    def list_promoted_jobs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM reindex_jobs
                WHERE promotion_status = 'promoted'
                ORDER BY COALESCE(promoted_at, finished_at, created_at) DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [self._normalize(row) for row in rows]

    def get_latest_job(self) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM reindex_jobs ORDER BY created_at DESC LIMIT 1",
            ).fetchone()
        return self._normalize(row) if row else None

    def mark_running(
        self,
        job_id: str,
        *,
        backend_type: str,
        output_collection_name: str,
        output_vector_dim: int,
        output_embedding_fingerprint: str,
    ) -> None:
        now = self.db.now()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE reindex_jobs
                SET status = 'running',
                    started_at = COALESCE(started_at, ?),
                    last_progress_at = ?,
                    last_error = '',
                    backend_type = ?,
                    output_collection_name = ?,
                    output_vector_dim = ?,
                    output_embedding_fingerprint = ?
                WHERE job_id = ?
                """,
                (
                    now,
                    now,
                    backend_type,
                    output_collection_name,
                    int(output_vector_dim),
                    output_embedding_fingerprint,
                    job_id,
                ),
            )

    def request_cancel(self, job_id: str, *, reason: str = "user_request") -> None:
        now = self.db.now()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE reindex_jobs
                SET status = CASE
                        WHEN status IN ('queued', 'running', 'cancel_requested') THEN 'cancel_requested'
                        ELSE status
                    END,
                    cancel_requested_at = COALESCE(cancel_requested_at, ?),
                    cancel_reason = ?,
                    last_progress_at = ?
                WHERE job_id = ?
                """,
                (now, reason[:240], now, job_id),
            )

    def is_cancel_requested(self, job_id: str) -> bool:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT status FROM reindex_jobs WHERE job_id = ? LIMIT 1",
                (job_id,),
            ).fetchone()
        if not row:
            return False
        return str(row["status"] or "") == "cancel_requested"

    def mark_cancelled(
        self,
        job_id: str,
        *,
        completion_reason: str = "cancelled_by_user",
        cancel_reason: str = "",
        last_error: str = "",
    ) -> None:
        now = self.db.now()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE reindex_jobs
                SET status = 'cancelled',
                    promotion_status = 'not_ready',
                    completion_reason = ?,
                    cancelled_at = ?,
                    finished_at = ?,
                    cancel_reason = CASE WHEN ? != '' THEN ? ELSE cancel_reason END,
                    last_error = ?,
                    last_progress_at = ?
                WHERE job_id = ?
                """,
                (
                    completion_reason,
                    now,
                    now,
                    cancel_reason[:240],
                    cancel_reason[:240],
                    last_error[:1000],
                    now,
                    job_id,
                ),
            )

    def mark_completed(self, job_id: str, *, completion_reason: str = "success", last_error: str = "") -> None:
        now = self.db.now()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE reindex_jobs
                SET status = 'completed',
                    completion_reason = ?,
                    finished_at = ?,
                    last_error = ?,
                    last_progress_at = ?
                WHERE job_id = ?
                """,
                (completion_reason, now, last_error[:1000], now, job_id),
            )

    def mark_completed_with_errors(
        self,
        job_id: str,
        *,
        completion_reason: str = "partial_success",
        last_error: str = "",
    ) -> None:
        now = self.db.now()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE reindex_jobs
                SET status = 'completed_with_errors',
                    completion_reason = ?,
                    finished_at = ?,
                    last_error = ?,
                    last_progress_at = ?
                WHERE job_id = ?
                """,
                (completion_reason, now, last_error[:1000], now, job_id),
            )

    def mark_failed(self, job_id: str, *, completion_reason: str, last_error: str) -> None:
        now = self.db.now()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE reindex_jobs
                SET status = 'failed',
                    promotion_status = 'rejected',
                    completion_reason = ?,
                    finished_at = ?,
                    last_error = ?,
                    last_progress_at = ?
                WHERE job_id = ?
                """,
                (completion_reason, now, last_error[:1000], now, job_id),
            )

    def increment_progress(
        self,
        job_id: str,
        *,
        processed: int = 0,
        succeeded: int = 0,
        failed: int = 0,
        skipped: int = 0,
        warning_increment: int = 0,
        last_error: str = "",
    ) -> None:
        now = self.db.now()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE reindex_jobs
                SET processed_chunks = processed_chunks + ?,
                    succeeded_chunks = succeeded_chunks + ?,
                    failed_chunks = failed_chunks + ?,
                    skipped_chunks = skipped_chunks + ?,
                    warning_count = warning_count + ?,
                    last_error = CASE WHEN ? != '' THEN ? ELSE last_error END,
                    last_progress_at = ?
                WHERE job_id = ?
                """,
                (
                    int(processed),
                    int(succeeded),
                    int(failed),
                    int(skipped),
                    int(warning_increment),
                    last_error[:1000],
                    last_error[:1000],
                    now,
                    job_id,
                ),
            )

    def touch_progress(self, job_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE reindex_jobs SET last_progress_at = ? WHERE job_id = ?",
                (self.db.now(), job_id),
            )

    def set_health_check_result(self, job_id: str, *, passed: bool, summary: dict[str, Any]) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE reindex_jobs
                SET health_check_passed = ?,
                    health_check_summary_json = ?,
                    last_progress_at = ?
                WHERE job_id = ?
                """,
                (1 if passed else 0, json.dumps(summary, ensure_ascii=False), self.db.now(), job_id),
            )

    def set_promotion_status(self, job_id: str, *, status: str, completion_reason: str | None = None) -> None:
        now = self.db.now()
        if completion_reason is None:
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE reindex_jobs SET promotion_status = ?, last_progress_at = ? WHERE job_id = ?",
                    (status, now, job_id),
                )
            return
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE reindex_jobs
                SET promotion_status = ?, completion_reason = ?, last_progress_at = ?
                WHERE job_id = ?
                """,
                (status, completion_reason, now, job_id),
            )

    def mark_promoted(self, job_id: str) -> None:
        now = self.db.now()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE reindex_jobs
                SET promotion_status = 'promoted',
                    promoted_at = ?,
                    last_progress_at = ?
                WHERE job_id = ?
                """,
                (now, now, job_id),
            )

    def replace_failed_chunks(self, job_id: str, failures: list[dict[str, Any]]) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM reindex_job_failed_chunks WHERE job_id = ?", (job_id,))
            for failure in failures:
                conn.execute(
                    """
                    INSERT INTO reindex_job_failed_chunks (
                        id, job_id, chunk_id, error_category, error_message, created_at, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"rfc_{uuid.uuid4().hex[:16]}",
                        job_id,
                        str(failure.get("chunk_id", "") or ""),
                        str(failure.get("error_category", "") or "")[:120],
                        str(failure.get("error_message", "") or "")[:1000],
                        self.db.now(),
                        json.dumps(failure.get("metadata") or {}, ensure_ascii=False),
                    ),
                )

    def add_failed_chunk(
        self,
        job_id: str,
        *,
        chunk_id: str,
        error_category: str,
        error_message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO reindex_job_failed_chunks (
                    id, job_id, chunk_id, error_category, error_message, created_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"rfc_{uuid.uuid4().hex[:16]}",
                    job_id,
                    chunk_id,
                    error_category[:120],
                    error_message[:1000],
                    self.db.now(),
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )

    def clear_failed_chunks(self, job_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM reindex_job_failed_chunks WHERE job_id = ?", (job_id,))

    def get_failed_chunks(self, job_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM reindex_job_failed_chunks
                WHERE job_id = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (job_id, int(limit)),
            ).fetchall()
        return [self._normalize_failed_chunk(row) for row in rows]

    def get_failed_chunk_ids(self, job_id: str, *, limit: int = 500) -> list[str]:
        return [str(item.get("chunk_id", "") or "") for item in self.get_failed_chunks(job_id, limit=limit)]

    def summarize_failed_chunks(self, job_id: str) -> dict[str, int]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT error_category, COUNT(1) AS count
                FROM reindex_job_failed_chunks
                WHERE job_id = ?
                GROUP BY error_category
                """,
                (job_id,),
            ).fetchall()
        return {str(row["error_category"] or "unknown") or "unknown": int(row["count"] or 0) for row in rows}

    def _normalize_failed_chunk(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        try:
            data["metadata"] = json.loads(str(data.get("metadata_json") or "{}"))
        except Exception:
            data["metadata"] = {}
        return data

    def _normalize(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        try:
            metadata = json.loads(str(data.get("metadata_json") or "{}"))
        except Exception:
            metadata = {}
        try:
            health_summary = json.loads(str(data.get("health_check_summary_json") or "{}"))
        except Exception:
            health_summary = {}
        data["metadata"] = metadata
        data["health_check_summary"] = health_summary
        return data
