from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.rag.reindex_job import ReindexJob
from app.system_notifications.notification_model import SystemNotification
from app.system_notifications.notification_types import (
    ACTION_REQUIRED,
    CRITICAL,
    DECISION_PENDING_TOO_LONG,
    EMBEDDING_CHANGED_NOT_REINDEXED,
    REINDEX_COMPLETED_WITH_ERRORS,
    REINDEX_ELIGIBLE,
    REINDEX_FAILED,
    SUGGESTION,
    VECTOR_BACKEND_DEGRADED,
)


def build_reindex_eligible(job: ReindexJob, *, now_iso: str) -> SystemNotification:
    return SystemNotification(
        id="",
        canonical_key=f"reindex_eligible:{job.job_id}",
        type=REINDEX_ELIGIBLE,
        level=ACTION_REQUIRED,
        title="有新的知识索引可切换",
        message=f"索引 {job.target_fingerprint} 已通过健康检查。可以 Promote 为 active collection。",
        created_at=now_iso,
        updated_at=now_iso,
        related_entity_type="reindex_job",
        related_entity_id=job.job_id,
        metadata={"page": "jobs", "job_id": job.job_id, "fingerprint": job.target_fingerprint},
    )


def build_reindex_failed(job: ReindexJob, *, now_iso: str) -> SystemNotification:
    return SystemNotification(
        id="",
        canonical_key=f"reindex_failed:{job.job_id}",
        type=REINDEX_FAILED,
        level=CRITICAL,
        title="知识索引构建失败",
        message=f"任务 {job.job_id} 失败。建议查看错误并重新构建。",
        created_at=now_iso,
        updated_at=now_iso,
        related_entity_type="reindex_job",
        related_entity_id=job.job_id,
        metadata={"page": "jobs", "job_id": job.job_id, "error": job.last_error},
    )


def build_reindex_partial(job: ReindexJob, *, now_iso: str) -> SystemNotification:
    return SystemNotification(
        id="",
        canonical_key=f"reindex_partial:{job.job_id}",
        type=REINDEX_COMPLETED_WITH_ERRORS,
        level=SUGGESTION,
        title="索引完成，但仍有失败 chunk",
        message=f"任务 {job.job_id} 已完成，但有 {job.failed_chunks} 个失败 chunk 和 {job.warning_count} 条警告。可查看详情或执行 Retry Failed Chunks。",
        created_at=now_iso,
        updated_at=now_iso,
        related_entity_type="reindex_job",
        related_entity_id=job.job_id,
        metadata={"page": "jobs", "job_id": job.job_id, "failed_chunks": job.failed_chunks, "warning_count": job.warning_count},
    )


def build_decision_pending_too_long(item: dict[str, object], *, threshold_hours: int, now_iso: str) -> SystemNotification:
    title = str(item.get("title", "") or "待确认决策").strip() or "待确认决策"
    return SystemNotification(
        id="",
        canonical_key=f"pending_decision:{item.get('id', '')}",
        type=DECISION_PENDING_TOO_LONG,
        level=SUGGESTION,
        title="有决策长时间未确认",
        message=f"{title} 已超过 {threshold_hours} 小时未处理。建议确认或拒绝，避免知识层持续悬空。",
        created_at=now_iso,
        updated_at=now_iso,
        related_entity_type="decision",
        related_entity_id=str(item.get("id", "") or ""),
        metadata={"page": "decisions", "decision_id": str(item.get("id", "") or ""), "title": title},
    )


def build_embedding_changed(current_fingerprint: str, active_fingerprint: str, *, now_iso: str) -> SystemNotification:
    return SystemNotification(
        id="",
        canonical_key=f"embedding_changed:{current_fingerprint}",
        type=EMBEDDING_CHANGED_NOT_REINDEXED,
        level=ACTION_REQUIRED,
        title="Embedding 已变更，但索引尚未重建",
        message=f"当前配置对应 {current_fingerprint}，但 active fingerprint 仍是 {active_fingerprint or '未设置'}。建议重建 embeddings。",
        created_at=now_iso,
        updated_at=now_iso,
        related_entity_type="settings",
        related_entity_id="rag_settings",
        metadata={"page": "jobs", "target_fingerprint": current_fingerprint, "active_fingerprint": active_fingerprint},
    )


def build_vector_backend_degraded(requested_backend: str, actual_backend: str, *, now_iso: str) -> SystemNotification:
    return SystemNotification(
        id="",
        canonical_key=f"backend_degraded:{requested_backend}:{actual_backend}",
        type=VECTOR_BACKEND_DEGRADED,
        level=CRITICAL if requested_backend == "chroma" else SUGGESTION,
        title="向量后端已降级",
        message=f"当前期望后端为 {requested_backend}，实际运行后端为 {actual_backend}。这通常意味着持久化能力或性能策略发生了退化。",
        created_at=now_iso,
        updated_at=now_iso,
        related_entity_type="settings",
        related_entity_id="rag_settings",
        metadata={"page": "settings", "requested_backend": requested_backend, "actual_backend": actual_backend},
    )


def is_older_than_hours(timestamp: str, hours: int) -> bool:
    if not timestamp:
        return False
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt <= datetime.now(timezone.utc) - timedelta(hours=hours)
