from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.rag.rag_manager import RagManager
from app.rag.rag_schema import RAGSettings
from app.rag.reindex_manager import ReindexManager
from app.storage.db import AppDatabase
from app.storage.repositories import MemoryRepo, ReindexJobRepo
from app.system_notifications import NotificationEngine
from app.system_notifications.notification_types import (
    DECISION_PENDING_TOO_LONG,
    EMBEDDING_CHANGED_NOT_REINDEXED,
    REINDEX_COMPLETED_WITH_ERRORS,
    REINDEX_ELIGIBLE,
    REINDEX_FAILED,
)
from app.ui.pages.jobs_page import build_health_check_rows


class SystemNotificationsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = Path("data/test_system_notifications")
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.db = AppDatabase(self.test_dir / "fairy_test.db")
        self.rag = RagManager(self.db)
        self.reindex = ReindexManager(self.db)
        self.repo = ReindexJobRepo(self.db)
        self.memory_repo = MemoryRepo(self.db)
        self.rag.save_settings(
            RAGSettings(
                embedding_enabled=True,
                embedding_provider="hash",
                vector_backend="sqlite",
                active_embedding_fingerprint="hash-v0",
                system_notifications_enabled=True,
                pending_decision_alert_hours=1,
            )
        )
        self.engine = NotificationEngine(rag_manager=self.rag, reindex_manager=self.reindex)

    def tearDown(self) -> None:
        for file_path in self.test_dir.glob("*"):
            if file_path.is_file():
                file_path.unlink(missing_ok=True)

    def _create_job(self, *, status: str, promotion_status: str = "not_ready", failed_chunks: int = 0, warning_count: int = 0) -> dict:
        row = self.repo.create_job(
            source_fingerprint="hash-v0",
            target_fingerprint="hash-v1",
            target_provider="hash",
            target_model="hash-v1",
            scope_type="full",
            total_chunks=5,
            status="queued",
            metadata={"reason": "test"},
        )
        job_id = str(row["job_id"])
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE reindex_jobs
                SET status = ?, promotion_status = ?, failed_chunks = ?, warning_count = ?,
                    finished_at = ?, health_check_passed = ?
                WHERE job_id = ?
                """,
                (
                    status,
                    promotion_status,
                    failed_chunks,
                    warning_count,
                    self.db.now(),
                    1 if promotion_status == "eligible" else 0,
                    job_id,
                ),
            )
        return self.repo.get_job(job_id) or {}

    def test_engine_generates_required_notification_types(self) -> None:
        self._create_job(status="completed", promotion_status="eligible")
        self._create_job(status="failed", promotion_status="rejected")
        self._create_job(status="completed_with_errors", promotion_status="eligible", failed_chunks=2, warning_count=1)
        decision_id = self.memory_repo.save_item(
            memory_type="decision_card",
            title="Decision pending",
            content="结论：需要人工确认。",
            decision_status="pending",
        )
        stale = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE memory_items SET created_at = ?, updated_at = ? WHERE id = ?",
                (stale, stale, decision_id),
            )

        notifications = self.engine.scan_all()
        types = {item.type for item in notifications}
        self.assertIn(REINDEX_ELIGIBLE, types)
        self.assertIn(REINDEX_FAILED, types)
        self.assertIn(REINDEX_COMPLETED_WITH_ERRORS, types)
        self.assertIn(DECISION_PENDING_TOO_LONG, types)
        self.assertIn(EMBEDDING_CHANGED_NOT_REINDEXED, types)

    def test_same_conditions_do_not_duplicate_notifications(self) -> None:
        self._create_job(status="completed", promotion_status="eligible")
        first = self.engine.scan_all()
        second = self.engine.scan_all()
        self.assertEqual(len(first), len(second))
        with self.db.connect() as conn:
            count = conn.execute("SELECT COUNT(1) AS count FROM system_notifications").fetchone()["count"]
        self.assertEqual(int(count), len(first))

    def test_dismiss_and_snooze_hide_notifications(self) -> None:
        self._create_job(status="completed", promotion_status="eligible")
        notifications = self.engine.scan_all()
        self.assertTrue(notifications)
        eligible = next(item for item in notifications if item.type == REINDEX_ELIGIBLE)
        self.engine.dismiss(eligible.id)
        remaining_types = {item.type for item in self.engine.list_active()}
        self.assertNotIn(REINDEX_ELIGIBLE, remaining_types)

        self._create_job(status="failed", promotion_status="rejected")
        notifications = self.engine.scan_all()
        target = next(item for item in notifications if item.type == REINDEX_FAILED)
        self.engine.snooze(target.id, hours=4)
        remaining_types = {item.type for item in self.engine.list_active()}
        self.assertNotIn(REINDEX_FAILED, remaining_types)

    def test_health_check_rows_are_parsed_for_ui(self) -> None:
        rows = build_health_check_rows(
            {
                "processed_ratio": 1.0,
                "failed_ratio": 0.0,
                "collection_readable": True,
                "vector_dim_match": True,
                "sample_lookup_passed": True,
                "query_smoke_test_passed": False,
            }
        )
        self.assertEqual(len(rows), 6)
        self.assertFalse(rows[-1]["passed"])


if __name__ == "__main__":
    unittest.main()
