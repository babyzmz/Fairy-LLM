from __future__ import annotations

import time
import unittest
from pathlib import Path

from app.rag.embedding_service import EmbeddingRuntime, HashEmbeddingService
from app.rag.rag_manager import RagManager
from app.rag.rag_schema import RAGSettings
from app.rag.reindex_manager import ReindexManager
from app.rag.vector_store import build_vector_store
from app.storage.db import AppDatabase
from app.storage.repositories.session_repo import SessionRepo


class TestableReindexManager(ReindexManager):
    def __init__(
        self,
        db: AppDatabase,
        *,
        target_fingerprint: str = "hash-v2",
        fail_chunk_ids: set[str] | None = None,
        fail_chunk_messages: dict[str, str] | None = None,
        fatal_backend: bool = False,
        force_validation_fail: bool = False,
        sleep_per_batch: float = 0.0,
    ) -> None:
        super().__init__(db)
        self.target_fingerprint = target_fingerprint
        self.fail_chunk_ids = set(fail_chunk_ids or set())
        self.fail_chunk_messages = dict(fail_chunk_messages or {})
        self.fatal_backend = fatal_backend
        self.force_validation_fail = force_validation_fail
        self.sleep_per_batch = sleep_per_batch
        self.HEALTH_MAX_FAILED_RATIO = 0.5
        self.seen_chunk_ids: list[str] = []

    def _build_runtime_from_settings(self, settings: RAGSettings) -> EmbeddingRuntime:
        hash_service = HashEmbeddingService()
        return EmbeddingRuntime(
            service=hash_service,
            provider_name="hash" if self.target_fingerprint.startswith("hash") else "test_provider",
            model_id=self.target_fingerprint,
            dimension=hash_service.dimension_hint,
            fingerprint=self.target_fingerprint,
            collection_name=f"test_collection_{self.target_fingerprint}",
        )

    def _create_vector_store(self, settings: RAGSettings, runtime: EmbeddingRuntime):
        if self.fatal_backend:
            raise RuntimeError("backend down")
        return build_vector_store(settings=settings, embedding_runtime=runtime, db=self.db)

    def _upsert_batch(self, vector_store, chunks):
        if self.sleep_per_batch > 0:
            time.sleep(self.sleep_per_batch)
        if len(chunks) > 1 and self.fail_chunk_ids:
            raise RuntimeError("batch fail for retry path")
        for chunk in chunks:
            self.seen_chunk_ids.append(chunk.id)
            if chunk.id in self.fail_chunk_ids:
                raise RuntimeError(self.fail_chunk_messages.get(chunk.id, f"chunk fail:{chunk.id}"))
        return super()._upsert_batch(vector_store, chunks)

    def validate_reindex_output(self, **kwargs):
        summary = super().validate_reindex_output(**kwargs)
        if self.force_validation_fail:
            summary["passed"] = False
            summary["reasons"] = list(summary.get("reasons") or []) + ["forced_validation_failure"]
        return summary


class ReindexManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = Path("data/test_reindex_manager")
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.db = AppDatabase(self.test_dir / "fairy_test.db")
        self.session_repo = SessionRepo(self.db)
        self.rag = RagManager(self.db)
        self.reindex = ReindexManager(self.db)
        self.rag.save_settings(
            RAGSettings(
                embedding_enabled=True,
                embedding_provider="hash",
                vector_backend="sqlite",
                reindex_enabled=True,
                reindex_batch_size=2,
            )
        )

    def tearDown(self) -> None:
        for file_path in self.test_dir.glob("*"):
            if file_path.is_file():
                file_path.unlink(missing_ok=True)

    def _create_chunks(self, *, count: int = 6) -> str:
        session_id = self.session_repo.create_session(
            title="Reindex Session",
            mode="normal_mode",
            provider="local",
            model="test",
        )
        for index in range(count):
            self.rag.save_session_summary(
                title=f"summary {index}",
                content=f"结论：当前规则 {index} 已明确，默认保持一致。",
                session_id=session_id,
            )
        return session_id

    def _wait_for_terminal(self, manager: ReindexManager, timeout: float = 8.0):
        deadline = time.time() + timeout
        latest = None
        while time.time() < deadline:
            latest = manager.get_latest_job()
            if latest is not None and latest.status not in {"queued", "running", "cancel_requested"}:
                return latest
            time.sleep(0.1)
        return latest

    def test_reindex_job_completed_and_promotable(self) -> None:
        self._create_chunks(count=6)
        settings = self.rag.load_settings()
        manager = TestableReindexManager(self.db, target_fingerprint="hash-v2")
        job = manager.start_reindex(settings=settings, source_fingerprint="hash-v1", scope_type="full", reason="test")
        latest = self._wait_for_terminal(manager)
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(job.job_id, latest.job_id)
        self.assertEqual(latest.status, "completed")
        self.assertEqual(latest.promotion_status, "eligible")
        result = manager.promote_job(latest.job_id)
        self.assertTrue(result["ok"])
        updated_settings = self.rag.load_settings()
        self.assertEqual(updated_settings.active_embedding_fingerprint, latest.target_fingerprint)
        promoted = manager.list_promoted_jobs(limit=5)
        self.assertTrue(promoted)
        self.assertEqual(promoted[0].job_id, latest.job_id)
        self.assertTrue(promoted[0].promoted_at)

    def test_reindex_job_completed_with_errors(self) -> None:
        self._create_chunks(count=6)
        rows = self.rag.document_repo.list_reindexable_chunks(limit=6)
        failing_id = str(rows[0]["id"])
        settings = self.rag.load_settings()
        manager = TestableReindexManager(self.db, target_fingerprint="hash-v3", fail_chunk_ids={failing_id})
        manager.start_reindex(settings=settings, source_fingerprint="hash-v1", scope_type="full", reason="partial")
        latest = self._wait_for_terminal(manager)
        assert latest is not None
        self.assertEqual(latest.status, "completed_with_errors")
        self.assertGreaterEqual(latest.failed_chunks, 1)
        self.assertEqual(latest.promotion_status, "eligible")

    def test_reindex_job_fatal_failed(self) -> None:
        self._create_chunks(count=3)
        settings = self.rag.load_settings()
        manager = TestableReindexManager(self.db, target_fingerprint="hash-v4", fatal_backend=True)
        manager.start_reindex(settings=settings, source_fingerprint="hash-v1", scope_type="full", reason="fatal")
        latest = self._wait_for_terminal(manager)
        assert latest is not None
        self.assertEqual(latest.status, "failed")
        self.assertEqual(latest.promotion_status, "rejected")
        self.assertIn(latest.completion_reason, {"backend_unavailable", "embedding_provider_error"})

    def test_cancel_requested_to_cancelled(self) -> None:
        self._create_chunks(count=12)
        settings = self.rag.load_settings()
        settings.reindex_batch_size = 1
        manager = TestableReindexManager(self.db, target_fingerprint="hash-v5", sleep_per_batch=0.15)
        job = manager.start_reindex(settings=settings, source_fingerprint="hash-v1", scope_type="full", reason="cancel")
        time.sleep(0.2)
        self.assertTrue(manager.request_cancel(job.job_id, reason="test_cancel"))
        latest = self._wait_for_terminal(manager, timeout=10.0)
        assert latest is not None
        self.assertEqual(latest.status, "cancelled")
        self.assertEqual(latest.completion_reason, "cancelled_by_user")

    def test_validation_rejected(self) -> None:
        self._create_chunks(count=4)
        settings = self.rag.load_settings()
        manager = TestableReindexManager(self.db, target_fingerprint="hash-v6", force_validation_fail=True)
        manager.start_reindex(settings=settings, source_fingerprint="hash-v1", scope_type="full", reason="invalid")
        latest = self._wait_for_terminal(manager)
        assert latest is not None
        self.assertEqual(latest.promotion_status, "rejected")
        self.assertIn(latest.status, {"failed", "completed_with_errors"})

    def test_promote_requires_eligible_job(self) -> None:
        self._create_chunks(count=3)
        settings = self.rag.load_settings()
        manager = TestableReindexManager(self.db, target_fingerprint="hash-v7", force_validation_fail=True)
        job = manager.start_reindex(settings=settings, source_fingerprint="hash-v1", scope_type="full", reason="not_promotable")
        latest = self._wait_for_terminal(manager)
        assert latest is not None
        result = manager.promote_job(job.job_id)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "not_eligible")

    def test_sqlite_multi_fingerprint_coexistence_and_active_switch(self) -> None:
        self._create_chunks(count=4)
        settings = self.rag.load_settings()
        manager = TestableReindexManager(self.db, target_fingerprint="hash-v8")
        manager.start_reindex(settings=settings, source_fingerprint="hash-v1", scope_type="full", reason="coexist")
        latest = self._wait_for_terminal(manager)
        assert latest is not None

        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT embedding_fingerprint, COUNT(1) AS count
                FROM chunk_embeddings_store
                GROUP BY embedding_fingerprint
                ORDER BY embedding_fingerprint
                """
            ).fetchall()
        grouped = {str(row["embedding_fingerprint"]): int(row["count"]) for row in rows}
        self.assertIn("hash-v1", grouped)
        self.assertIn("hash-v8", grouped)
        self.assertNotEqual(self.rag.load_settings().active_embedding_fingerprint, "hash-v8")
        promote = manager.promote_job(latest.job_id)
        self.assertTrue(promote["ok"])
        self.assertEqual(self.rag.load_settings().active_embedding_fingerprint, "hash-v8")

    def test_retry_failed_chunks_creates_child_job_and_only_reprocesses_failed_ids(self) -> None:
        self._create_chunks(count=5)
        rows = self.rag.document_repo.list_reindexable_chunks(limit=5)
        failing_id = str(rows[0]["id"])
        settings = self.rag.load_settings()

        parent_manager = TestableReindexManager(self.db, target_fingerprint="hash-v9", fail_chunk_ids={failing_id})
        parent_manager.start_reindex(settings=settings, source_fingerprint="hash-v1", scope_type="full", reason="parent")
        parent = self._wait_for_terminal(parent_manager)
        assert parent is not None
        self.assertEqual(parent.status, "completed_with_errors")
        self.assertEqual(parent.failed_chunks, 1)
        self.assertEqual(parent_manager.job_repo.get_failed_chunk_ids(parent.job_id), [failing_id])

        retry_manager = TestableReindexManager(self.db, target_fingerprint="hash-v9")
        retry_job = retry_manager.retry_failed_chunks(parent.job_id)
        repaired = self._wait_for_terminal(retry_manager)
        assert repaired is not None
        self.assertEqual(retry_job.job_id, repaired.job_id)
        self.assertEqual(repaired.status, "completed")
        self.assertEqual(repaired.parent_job_id, parent.job_id)
        self.assertEqual(repaired.retry_source_job_id, parent.job_id)
        self.assertEqual(repaired.retry_mode, "failed_chunks_only")
        self.assertEqual(repaired.backend_type, parent.backend_type)
        self.assertEqual(repaired.target_provider, parent.target_provider)
        self.assertEqual(repaired.target_model, parent.target_model)
        self.assertEqual(repaired.target_fingerprint, parent.target_fingerprint)
        self.assertEqual(sorted(retry_manager.seen_chunk_ids), [failing_id])

    def test_retry_failed_chunks_by_reason_filter(self) -> None:
        self._create_chunks(count=6)
        rows = self.rag.document_repo.list_reindexable_chunks(limit=6)
        embedding_id = str(rows[0]["id"])
        backend_id = str(rows[1]["id"])
        settings = self.rag.load_settings()

        parent_manager = TestableReindexManager(
            self.db,
            target_fingerprint="hash-v10",
            fail_chunk_ids={embedding_id, backend_id},
            fail_chunk_messages={
                embedding_id: "embedding provider timeout",
                backend_id: "backend storage unavailable",
            },
        )
        parent_manager.start_reindex(settings=settings, source_fingerprint="hash-v1", scope_type="full", reason="parent-filter")
        parent = self._wait_for_terminal(parent_manager)
        assert parent is not None
        counts = parent_manager.get_failed_chunk_category_counts(parent.job_id)
        self.assertEqual(counts.get("embedding_provider_error"), 1)
        self.assertEqual(counts.get("backend_error"), 1)

        retry_manager = TestableReindexManager(self.db, target_fingerprint="hash-v10")
        retry_job = retry_manager.retry_failed_chunks(
            parent.job_id,
            reason="ui_retry_failed_embedding_errors",
            reason_filter="embedding_provider_error",
        )
        repaired = self._wait_for_terminal(retry_manager)
        assert repaired is not None
        self.assertEqual(retry_job.job_id, repaired.job_id)
        self.assertEqual(repaired.metadata.get("retry_reason_filter"), "embedding_provider_error")
        self.assertEqual(sorted(retry_manager.seen_chunk_ids), [embedding_id])


if __name__ == "__main__":
    unittest.main()
