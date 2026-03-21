PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    mode TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'text',
    token_estimate INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    memory_type TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    importance REAL NOT NULL DEFAULT 0.5,
    decision_status TEXT NOT NULL DEFAULT 'confirmed',
    source_session_id TEXT,
    source_message_ids_json TEXT NOT NULL DEFAULT '[]',
    evidence_count INTEGER NOT NULL DEFAULT 1,
    canonical_key TEXT,
    merged_source_refs_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY(source_session_id) REFERENCES sessions(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    file_path TEXT,
    mime_type TEXT,
    checksum TEXT,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT,
    source_kind TEXT NOT NULL,
    source_ref_id TEXT NOT NULL,
    embedding_fingerprint TEXT NOT NULL DEFAULT '',
    chunk_index INTEGER NOT NULL DEFAULT 0,
    content TEXT NOT NULL,
    token_estimate INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

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
);

CREATE TABLE IF NOT EXISTS action_logs (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    event_type TEXT NOT NULL,
    event_data_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

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
    promoted_at TEXT,
    health_check_passed INTEGER NOT NULL DEFAULT 0,
    health_check_summary_json TEXT NOT NULL DEFAULT '{}',
    last_progress_at TEXT,
    last_error TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

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
);

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
);

CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_mode ON sessions(mode);
CREATE INDEX IF NOT EXISTS idx_messages_session_created ON messages(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_type_updated ON memory_items(memory_type, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_session ON memory_items(source_session_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_canonical ON memory_items(canonical_key);
CREATE INDEX IF NOT EXISTS idx_memory_decision_status ON memory_items(decision_status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_documents_created ON documents(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_document_chunks_document ON document_chunks(document_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_document_chunks_source_ref ON document_chunks(source_ref_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_document_chunks_kind_created ON document_chunks(source_kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_document_chunks_fingerprint ON document_chunks(embedding_fingerprint, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_logs_session_created ON action_logs(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_logs_type_created ON action_logs(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_store_source_ref ON chunk_embeddings_store(source_ref_id);
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_store_fingerprint ON chunk_embeddings_store(embedding_fingerprint);
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_store_chunk ON chunk_embeddings_store(chunk_id);
CREATE INDEX IF NOT EXISTS idx_reindex_jobs_status_created ON reindex_jobs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reindex_jobs_target_fingerprint ON reindex_jobs(target_fingerprint, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reindex_jobs_promotion_status ON reindex_jobs(promotion_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reindex_jobs_promoted_at ON reindex_jobs(promoted_at DESC);
CREATE INDEX IF NOT EXISTS idx_reindex_failed_chunks_job ON reindex_job_failed_chunks(job_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reindex_failed_chunks_chunk ON reindex_job_failed_chunks(chunk_id);
CREATE INDEX IF NOT EXISTS idx_system_notifications_active ON system_notifications(is_completed, is_dismissed, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_system_notifications_related ON system_notifications(related_entity_type, related_entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_system_notifications_type ON system_notifications(type, created_at DESC);
