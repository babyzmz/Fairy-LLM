from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ReindexJob:
    job_id: str
    status: str
    created_at: str
    promotion_status: str = "not_ready"
    completion_reason: str = ""
    started_at: str = ""
    finished_at: str = ""
    cancel_requested_at: str = ""
    cancelled_at: str = ""
    cancel_reason: str = ""
    promoted_at: str = ""
    last_progress_at: str = ""
    source_fingerprint: str = ""
    target_fingerprint: str = ""
    target_provider: str = ""
    target_model: str = ""
    backend_type: str = ""
    output_collection_name: str = ""
    output_vector_dim: int = 0
    output_embedding_fingerprint: str = ""
    total_chunks: int = 0
    processed_chunks: int = 0
    succeeded_chunks: int = 0
    failed_chunks: int = 0
    skipped_chunks: int = 0
    warning_count: int = 0
    health_check_passed: bool = False
    health_check_summary: dict[str, Any] = field(default_factory=dict)
    last_error: str = ""
    scope_type: str = "full"
    scope_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def progress_ratio(self) -> float:
        if self.total_chunks <= 0:
            return 0.0
        return min(1.0, max(0.0, self.processed_chunks / max(1, self.total_chunks)))

    @property
    def is_promotable(self) -> bool:
        return self.promotion_status == "eligible" and self.status in {"completed", "completed_with_errors"}

    @property
    def parent_job_id(self) -> str:
        return str(self.metadata.get("parent_job_id", "") or "")

    @property
    def retry_mode(self) -> str:
        return str(self.metadata.get("retry_mode", "") or "")

    @property
    def is_retry_job(self) -> bool:
        return bool(self.parent_job_id and self.retry_mode)

    @property
    def retry_source_job_id(self) -> str:
        value = str(self.metadata.get("retry_source_job_id", "") or "")
        return value or self.parent_job_id

    def health_check_items(self) -> list[dict[str, Any]]:
        summary = self.health_check_summary or {}
        processed_ratio = float(summary.get("processed_ratio", 0.0) or 0.0)
        failed_ratio = float(summary.get("failed_ratio", 0.0) or 0.0)
        items = [
            {
                "key": "processed_ratio",
                "label": "Processed ratio",
                "value": f"{processed_ratio:.2%}",
                "passed": processed_ratio >= 0.98,
            },
            {
                "key": "failed_ratio",
                "label": "Failed ratio",
                "value": f"{failed_ratio:.2%}",
                "passed": failed_ratio <= 0.02,
            },
            {
                "key": "collection_readable",
                "label": "Collection readable",
                "value": str(bool(summary.get("collection_readable", False))),
                "passed": bool(summary.get("collection_readable", False)),
            },
            {
                "key": "vector_dim_match",
                "label": "Vector dim match",
                "value": str(bool(summary.get("vector_dim_match", False))),
                "passed": bool(summary.get("vector_dim_match", False)),
            },
            {
                "key": "sample_lookup_passed",
                "label": "Sample lookup",
                "value": str(bool(summary.get("sample_lookup_passed", False))),
                "passed": bool(summary.get("sample_lookup_passed", False)),
            },
            {
                "key": "query_smoke_test_passed",
                "label": "Query smoke test",
                "value": str(bool(summary.get("query_smoke_test_passed", False))),
                "passed": bool(summary.get("query_smoke_test_passed", False)),
            },
        ]
        return items

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "ReindexJob":
        metadata = dict(row.get("metadata") or {})
        health_summary = dict(row.get("health_check_summary") or {})
        return cls(
            job_id=str(row.get("job_id", "") or ""),
            status=str(row.get("status", "") or ""),
            created_at=str(row.get("created_at", "") or ""),
            promotion_status=str(row.get("promotion_status", "not_ready") or "not_ready"),
            completion_reason=str(row.get("completion_reason", "") or ""),
            started_at=str(row.get("started_at", "") or ""),
            finished_at=str(row.get("finished_at", "") or ""),
            cancel_requested_at=str(row.get("cancel_requested_at", "") or ""),
            cancelled_at=str(row.get("cancelled_at", "") or ""),
            cancel_reason=str(row.get("cancel_reason", "") or ""),
            promoted_at=str(row.get("promoted_at", "") or ""),
            last_progress_at=str(row.get("last_progress_at", "") or ""),
            source_fingerprint=str(row.get("source_fingerprint", "") or ""),
            target_fingerprint=str(row.get("target_fingerprint", "") or ""),
            target_provider=str(row.get("target_provider", "") or ""),
            target_model=str(row.get("target_model", "") or ""),
            backend_type=str(row.get("backend_type", "") or ""),
            output_collection_name=str(row.get("output_collection_name", "") or ""),
            output_vector_dim=int(row.get("output_vector_dim", 0) or 0),
            output_embedding_fingerprint=str(row.get("output_embedding_fingerprint", "") or ""),
            total_chunks=int(row.get("total_chunks", 0) or 0),
            processed_chunks=int(row.get("processed_chunks", 0) or 0),
            succeeded_chunks=int(row.get("succeeded_chunks", 0) or 0),
            failed_chunks=int(row.get("failed_chunks", 0) or 0),
            skipped_chunks=int(row.get("skipped_chunks", 0) or 0),
            warning_count=int(row.get("warning_count", 0) or 0),
            health_check_passed=bool(int(row.get("health_check_passed", 0) or 0)),
            health_check_summary=health_summary,
            last_error=str(row.get("last_error", "") or ""),
            scope_type=str(metadata.get("scope_type", "full") or "full"),
            scope_ref=str(metadata.get("scope_ref", "") or ""),
            metadata=metadata,
        )
