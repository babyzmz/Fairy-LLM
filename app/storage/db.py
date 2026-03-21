from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.config import BASE_DIR


APP_DB_PATH = BASE_DIR / "data" / "fairy_app.db"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
MIGRATION_STATEMENTS = (
    "ALTER TABLE memory_items ADD COLUMN evidence_count INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE memory_items ADD COLUMN canonical_key TEXT",
    "ALTER TABLE memory_items ADD COLUMN merged_source_refs_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE memory_items ADD COLUMN decision_status TEXT NOT NULL DEFAULT 'confirmed'",
    "ALTER TABLE document_chunks ADD COLUMN embedding_fingerprint TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE chunk_embeddings ADD COLUMN embedding_fingerprint TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE chunk_embeddings ADD COLUMN embedding_provider TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE chunk_embeddings ADD COLUMN embedding_model_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE chunk_embeddings ADD COLUMN embedding_dim INTEGER NOT NULL DEFAULT 0",
    "CREATE INDEX IF NOT EXISTS idx_memory_canonical ON memory_items(canonical_key)",
    "CREATE INDEX IF NOT EXISTS idx_memory_decision_status ON memory_items(decision_status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_document_chunks_fingerprint ON document_chunks(embedding_fingerprint, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_fingerprint ON chunk_embeddings(embedding_fingerprint)",
    """
    CREATE TABLE IF NOT EXISTS chunk_embeddings_store (
        id TEXT PRIMARY KEY,
        chunk_id TEXT NOT NULL,
        source_ref_id TEXT NOT NULL,
        embedding_fingerprint TEXT NOT NULL DEFAULT '',
        embedding_provider TEXT NOT NULL DEFAULT '',
        embedding_model_id TEXT NOT NULL DEFAULT '',
        embedding_dim INTEGER NOT NULL DEFAULT 0,
        content TEXT NOT NULL,
        embedding_json TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        FOREIGN KEY(chunk_id) REFERENCES document_chunks(id) ON DELETE CASCADE,
        UNIQUE(chunk_id, embedding_fingerprint)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_store_source_ref ON chunk_embeddings_store(source_ref_id)",
    "CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_store_fingerprint ON chunk_embeddings_store(embedding_fingerprint)",
    "CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_store_chunk ON chunk_embeddings_store(chunk_id)",
    """
    CREATE TABLE IF NOT EXISTS reindex_jobs (
        job_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        promotion_status TEXT NOT NULL DEFAULT 'not_ready',
        completion_reason TEXT NOT NULL DEFAULT '',
        source_fingerprint TEXT NOT NULL DEFAULT '',
        target_fingerprint TEXT NOT NULL DEFAULT '',
        target_provider TEXT NOT NULL DEFAULT '',
        target_model TEXT NOT NULL DEFAULT '',
        backend_type TEXT NOT NULL DEFAULT '',
        output_collection_name TEXT NOT NULL DEFAULT '',
        output_vector_dim INTEGER NOT NULL DEFAULT 0,
        output_embedding_fingerprint TEXT NOT NULL DEFAULT '',
        total_chunks INTEGER NOT NULL DEFAULT 0,
        processed_chunks INTEGER NOT NULL DEFAULT 0,
        succeeded_chunks INTEGER NOT NULL DEFAULT 0,
        failed_chunks INTEGER NOT NULL DEFAULT 0,
        skipped_chunks INTEGER NOT NULL DEFAULT 0,
        warning_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        cancel_requested_at TEXT,
        cancelled_at TEXT,
        cancel_reason TEXT NOT NULL DEFAULT '',
        health_check_passed INTEGER NOT NULL DEFAULT 0,
        health_check_summary_json TEXT NOT NULL DEFAULT '{}',
        last_progress_at TEXT,
        last_error TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    "ALTER TABLE reindex_jobs ADD COLUMN promotion_status TEXT NOT NULL DEFAULT 'not_ready'",
    "ALTER TABLE reindex_jobs ADD COLUMN completion_reason TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE reindex_jobs ADD COLUMN backend_type TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE reindex_jobs ADD COLUMN output_collection_name TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE reindex_jobs ADD COLUMN output_vector_dim INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE reindex_jobs ADD COLUMN output_embedding_fingerprint TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE reindex_jobs ADD COLUMN succeeded_chunks INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE reindex_jobs ADD COLUMN skipped_chunks INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE reindex_jobs ADD COLUMN warning_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE reindex_jobs ADD COLUMN cancel_requested_at TEXT",
    "ALTER TABLE reindex_jobs ADD COLUMN cancelled_at TEXT",
    "ALTER TABLE reindex_jobs ADD COLUMN cancel_reason TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE reindex_jobs ADD COLUMN promoted_at TEXT",
    "ALTER TABLE reindex_jobs ADD COLUMN health_check_passed INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE reindex_jobs ADD COLUMN health_check_summary_json TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE reindex_jobs ADD COLUMN last_progress_at TEXT",
    "CREATE INDEX IF NOT EXISTS idx_reindex_jobs_status_created ON reindex_jobs(status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_reindex_jobs_target_fingerprint ON reindex_jobs(target_fingerprint, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_reindex_jobs_promotion_status ON reindex_jobs(promotion_status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_reindex_jobs_promoted_at ON reindex_jobs(promoted_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS reindex_job_failed_chunks (
        id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        chunk_id TEXT NOT NULL,
        error_category TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(job_id) REFERENCES reindex_jobs(job_id) ON DELETE CASCADE,
        FOREIGN KEY(chunk_id) REFERENCES document_chunks(id) ON DELETE CASCADE
    )
    """,
    "ALTER TABLE reindex_job_failed_chunks ADD COLUMN error_category TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS idx_reindex_failed_chunks_job ON reindex_job_failed_chunks(job_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_reindex_failed_chunks_chunk ON reindex_job_failed_chunks(chunk_id)",
    """
    CREATE TABLE IF NOT EXISTS system_notifications (
        id TEXT PRIMARY KEY,
        canonical_key TEXT NOT NULL UNIQUE,
        type TEXT NOT NULL,
        level TEXT NOT NULL,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT,
        related_entity_type TEXT NOT NULL DEFAULT '',
        related_entity_id TEXT NOT NULL DEFAULT '',
        is_dismissed INTEGER NOT NULL DEFAULT 0,
        is_completed INTEGER NOT NULL DEFAULT 0,
        snooze_until TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_system_notifications_active ON system_notifications(is_completed, is_dismissed, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_system_notifications_related ON system_notifications(related_entity_type, related_entity_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_system_notifications_type ON system_notifications(type, created_at DESC)",
)


class AppDatabase:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or APP_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_lock = threading.Lock()
        self._initialized = False
        self.initialize()

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            schema = SCHEMA_PATH.read_text(encoding="utf-8")
            with self.connect() as conn:
                self._apply_schema(conn, schema)
                self._apply_migrations(conn)
            self._initialized = True

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _apply_schema(self, conn: sqlite3.Connection, schema: str) -> None:
        for raw_statement in schema.split(";"):
            statement = raw_statement.strip()
            if not statement:
                continue
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                # Existing databases can lag behind the newest schema. When an
                # index references a column that will be added by the migration
                # pass below, skip it here and let migrations recreate it.
                if statement.upper().startswith("CREATE INDEX") and "no such column" in str(exc).lower():
                    continue
                raise

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        for statement in MIGRATION_STATEMENTS:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                continue


_APP_DATABASE: AppDatabase | None = None
_APP_DATABASE_LOCK = threading.Lock()


def get_app_database() -> AppDatabase:
    global _APP_DATABASE
    if _APP_DATABASE is None:
        with _APP_DATABASE_LOCK:
            if _APP_DATABASE is None:
                _APP_DATABASE = AppDatabase()
    return _APP_DATABASE
