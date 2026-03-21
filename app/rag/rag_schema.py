from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class RAGSettings:
    rag_enabled: bool = True
    rag_top_k: int = 4
    rag_max_context_chars: int = 1400
    rag_use_for_history_queries: bool = True
    rag_use_for_document_qa: bool = True
    embedding_enabled: bool = True
    embedding_provider: str = "qwen_cloud"
    embedding_model: str = "text-embedding-v4"
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_key_ref: str = "DASHSCOPE_API_KEY"
    embedding_timeout_seconds: int = 25
    embedding_model_path: str = ""
    embedding_backend: str = "sentence_transformers"
    embedding_device: str = "cpu"
    embedding_batch_size: int = 16
    embedding_threads: int = 4
    active_embedding_fingerprint: str = ""
    auto_switch_collection_after_reindex: bool = False
    reindex_batch_size: int = 64
    reindex_enabled: bool = True
    vector_backend: str = "auto"
    chroma_persist_path: str = ""
    chroma_collection_name: str = "fairy_knowledge"
    enable_retrieval_debug_panel: bool = False
    importance_gating_enabled: bool = True
    importance_threshold_memory: int = 6
    importance_threshold_summary: int = 4
    dedup_enabled: bool = True
    dedup_similarity_threshold: float = 0.92
    session_rollup_enabled: bool = True
    session_rollup_turn_threshold: int = 6
    max_session_summaries_per_session: int = 2
    legacy_memory_mode: str = "read_only"
    system_notifications_enabled: bool = True
    notification_scan_interval_seconds: int = 60
    pending_decision_alert_hours: int = 24

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RAGSettings":
        data = data or {}
        return cls(
            rag_enabled=bool(data.get("rag_enabled", True)),
            rag_top_k=max(1, int(data.get("rag_top_k", 4) or 4)),
            rag_max_context_chars=max(400, int(data.get("rag_max_context_chars", 1400) or 1400)),
            rag_use_for_history_queries=bool(data.get("rag_use_for_history_queries", True)),
            rag_use_for_document_qa=bool(data.get("rag_use_for_document_qa", True)),
            embedding_enabled=bool(data.get("embedding_enabled", True)),
            embedding_provider=str(data.get("embedding_provider", "qwen_cloud") or "qwen_cloud"),
            embedding_model=str(data.get("embedding_model", "text-embedding-v4") or "text-embedding-v4"),
            embedding_base_url=str(data.get("embedding_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1") or "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            embedding_api_key_ref=str(data.get("embedding_api_key_ref", "DASHSCOPE_API_KEY") or "DASHSCOPE_API_KEY"),
            embedding_timeout_seconds=max(5, int(data.get("embedding_timeout_seconds", 25) or 25)),
            embedding_model_path=str(data.get("embedding_model_path", "") or ""),
            embedding_backend=str(data.get("embedding_backend", "sentence_transformers") or "sentence_transformers"),
            embedding_device=str(data.get("embedding_device", "cpu") or "cpu"),
            embedding_batch_size=max(1, int(data.get("embedding_batch_size", 16) or 16)),
            embedding_threads=max(1, int(data.get("embedding_threads", 4) or 4)),
            active_embedding_fingerprint=str(data.get("active_embedding_fingerprint", "") or ""),
            auto_switch_collection_after_reindex=bool(data.get("auto_switch_collection_after_reindex", False)),
            reindex_batch_size=max(1, int(data.get("reindex_batch_size", 64) or 64)),
            reindex_enabled=bool(data.get("reindex_enabled", True)),
            vector_backend=str(data.get("vector_backend", "auto") or "auto"),
            chroma_persist_path=str(data.get("chroma_persist_path", "") or ""),
            chroma_collection_name=str(data.get("chroma_collection_name", "fairy_knowledge") or "fairy_knowledge"),
            enable_retrieval_debug_panel=bool(data.get("enable_retrieval_debug_panel", False)),
            importance_gating_enabled=bool(data.get("importance_gating_enabled", True)),
            importance_threshold_memory=max(1, int(data.get("importance_threshold_memory", 6) or 6)),
            importance_threshold_summary=max(1, int(data.get("importance_threshold_summary", 4) or 4)),
            dedup_enabled=bool(data.get("dedup_enabled", True)),
            dedup_similarity_threshold=float(data.get("dedup_similarity_threshold", 0.92) or 0.92),
            session_rollup_enabled=bool(data.get("session_rollup_enabled", True)),
            session_rollup_turn_threshold=max(2, int(data.get("session_rollup_turn_threshold", 6) or 6)),
            max_session_summaries_per_session=max(1, int(data.get("max_session_summaries_per_session", 2) or 2)),
            legacy_memory_mode=str(data.get("legacy_memory_mode", "read_only") or "read_only"),
            system_notifications_enabled=bool(data.get("system_notifications_enabled", True)),
            notification_scan_interval_seconds=max(15, int(data.get("notification_scan_interval_seconds", 60) or 60)),
            pending_decision_alert_hours=max(1, int(data.get("pending_decision_alert_hours", 24) or 24)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ChunkMetadata:
    source_kind: str
    source_ref_id: str
    title: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    session_id: str = ""
    importance: float = 0.0
    document_id: str = ""
    embedding_provider: str = ""
    embedding_model_id: str = ""
    embedding_dim: int = 0
    embedding_fingerprint: str = ""


@dataclass(slots=True)
class ChunkRecord:
    id: str
    content: str
    chunk_index: int
    token_estimate: int
    metadata: ChunkMetadata


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: str
    score: float
    content: str
    source_kind: str
    source_ref_id: str
    title: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    session_id: str = ""
    importance: float = 0.0
    document_id: str = ""
    embedding_fingerprint: str = ""


@dataclass(slots=True)
class RetrievalResult:
    query: str
    chunks: list[RetrievedChunk] = field(default_factory=list)
    prompt_block: str = ""
    debug_snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def injected_chars(self) -> int:
        return len(self.prompt_block)
