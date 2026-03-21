from __future__ import annotations

import logging
from typing import Any

from app.rag.rag_manager import RagManager
from app.rag.reindex_manager import ReindexManager
from app.system_notifications.notification_model import SystemNotification
from app.system_notifications.notification_rules import (
    build_decision_pending_too_long,
    build_embedding_changed,
    build_reindex_eligible,
    build_reindex_failed,
    build_reindex_partial,
    build_vector_backend_degraded,
    is_older_than_hours,
)
from app.system_notifications.notification_store import NotificationStore
from app.system_notifications.notification_types import MANAGED_NOTIFICATION_TYPES


logger = logging.getLogger(__name__)


class NotificationEngine:
    def __init__(
        self,
        *,
        rag_manager: RagManager,
        reindex_manager: ReindexManager,
        store: NotificationStore | None = None,
    ) -> None:
        self.rag_manager = rag_manager
        self.reindex_manager = reindex_manager
        self.store = store or NotificationStore(rag_manager.db)

    def list_active(self, *, limit: int = 50) -> list[SystemNotification]:
        return self.store.list_active(limit=limit)

    def list_recent(self, *, limit: int = 100) -> list[SystemNotification]:
        return self.store.list_recent(limit=limit)

    def dismiss(self, notification_id: str) -> None:
        self.store.dismiss(notification_id)

    def dismiss_many(self, notification_ids: list[str]) -> None:
        if notification_ids:
            self.store.dismiss_many(notification_ids)

    def snooze(self, notification_id: str, *, hours: int = 4) -> None:
        from datetime import datetime, timedelta, timezone

        until = datetime.now(timezone.utc) + timedelta(hours=max(1, hours))
        self.store.snooze(notification_id, until_iso=until.isoformat())

    def snooze_many(self, notification_ids: list[str], *, hours: int) -> None:
        from datetime import datetime, timedelta, timezone

        if not notification_ids:
            return
        until = datetime.now(timezone.utc) + timedelta(hours=max(1, hours))
        self.store.snooze_many(notification_ids, until_iso=until.isoformat())

    def snooze_until_tomorrow(self, notification_ids: list[str]) -> None:
        from datetime import datetime, time, timedelta, timezone

        if not notification_ids:
            return
        now = datetime.now(timezone.utc)
        tomorrow = (now + timedelta(days=1)).date()
        until = datetime.combine(tomorrow, time(hour=9, minute=0, tzinfo=timezone.utc))
        self.store.snooze_many(notification_ids, until_iso=until.isoformat())

    def mark_completed(self, notification_id: str) -> None:
        self.store.mark_completed(notification_id)

    def mark_completed_many(self, notification_ids: list[str]) -> None:
        if notification_ids:
            self.store.mark_completed_many(notification_ids)

    def scan_all(self) -> list[SystemNotification]:
        settings = self.rag_manager.load_settings()
        if not settings.system_notifications_enabled:
            return []

        candidates: list[SystemNotification] = []
        candidates.extend(self.scan_reindex_jobs())
        candidates.extend(self.scan_decisions(threshold_hours=settings.pending_decision_alert_hours))
        candidates.extend(self.scan_config_state())
        candidates.extend(self.scan_backend_state())
        notifications = self.store.sync(candidates=candidates, managed_types=MANAGED_NOTIFICATION_TYPES)
        logger.info("system_notifications_scanned count=%s", len(notifications))
        return notifications

    def scan_reindex_jobs(self) -> list[SystemNotification]:
        now = self.rag_manager.db.now()
        notifications: list[SystemNotification] = []
        for job in self.reindex_manager.list_jobs(limit=50):
            if job.status in {"completed", "completed_with_errors"} and job.promotion_status == "eligible":
                notifications.append(build_reindex_eligible(job, now_iso=now))
            if job.status == "failed":
                notifications.append(build_reindex_failed(job, now_iso=now))
            if job.status == "completed_with_errors" and (job.failed_chunks > 0 or job.warning_count > 0):
                notifications.append(build_reindex_partial(job, now_iso=now))
        return notifications

    def scan_decisions(self, *, threshold_hours: int) -> list[SystemNotification]:
        now = self.rag_manager.db.now()
        notifications: list[SystemNotification] = []
        for item in self.rag_manager.list_pending_decisions(limit=50):
            timestamp = str(item.get("updated_at", "") or item.get("created_at", "") or "")
            if is_older_than_hours(timestamp, threshold_hours):
                notifications.append(build_decision_pending_too_long(item, threshold_hours=threshold_hours, now_iso=now))
        return notifications

    def scan_config_state(self) -> list[SystemNotification]:
        settings = self.rag_manager.load_settings()
        try:
            configured_runtime = self.reindex_manager.preview_target_runtime(settings)
            configured_fingerprint = configured_runtime.fingerprint
        except Exception as exc:  # noqa: BLE001
            logger.warning("Notification engine failed to preview embedding runtime: %s", exc)
            return []

        active_fingerprint = settings.active_embedding_fingerprint.strip() or self.rag_manager.embedding_fingerprint
        if not configured_fingerprint or configured_fingerprint == active_fingerprint:
            return []

        has_pending_job = any(
            job.status in {"queued", "running", "cancel_requested"} and job.target_fingerprint == configured_fingerprint
            for job in self.reindex_manager.list_jobs(limit=30)
        )
        if has_pending_job:
            return []
        return [
            build_embedding_changed(
                configured_fingerprint,
                active_fingerprint,
                now_iso=self.rag_manager.db.now(),
            )
        ]

    def scan_backend_state(self) -> list[SystemNotification]:
        settings = self.rag_manager.load_settings()
        requested_backend = str(settings.vector_backend or "auto")
        actual_backend = self.rag_manager.vector_backend_name
        if requested_backend != "chroma":
            return []
        if actual_backend in {"chroma", "unknown"}:
            return []
        return [build_vector_backend_degraded(requested_backend, actual_backend, now_iso=self.rag_manager.db.now())]
