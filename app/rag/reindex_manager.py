from __future__ import annotations

import json
import logging
import threading
from typing import Any

from app.rag.embedding_service import EmbeddingRuntime, EmbeddingServiceFactory
from app.rag.rag_schema import ChunkMetadata, ChunkRecord, RAGSettings
from app.rag.reindex_job import ReindexJob
from app.rag.vector_store import BaseVectorStore, build_vector_store
from app.storage.db import AppDatabase, get_app_database
from app.storage.repositories import DocumentRepo, ReindexJobRepo, SettingsRepo


logger = logging.getLogger(__name__)


class ReindexManager:
    RAG_SETTINGS_KEY = "rag_settings"
    RUNTIME_REGISTRY_KEY = "rag_embedding_runtime_registry"
    HEALTH_MIN_PROCESSED_RATIO = 0.98
    HEALTH_MAX_FAILED_RATIO = 0.02

    def __init__(self, db: AppDatabase | None = None) -> None:
        self.db = db or get_app_database()
        self.settings_repo = SettingsRepo(self.db)
        self.document_repo = DocumentRepo(self.db)
        self.job_repo = ReindexJobRepo(self.db)
        self.embedding_factory = EmbeddingServiceFactory()
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def list_jobs(self, *, limit: int = 20) -> list[ReindexJob]:
        return [ReindexJob.from_row(row) for row in self.job_repo.list_jobs(limit=limit)]

    def list_promoted_jobs(self, *, limit: int = 20) -> list[ReindexJob]:
        return [ReindexJob.from_row(row) for row in self.job_repo.list_promoted_jobs(limit=limit)]

    def get_job(self, job_id: str) -> ReindexJob | None:
        row = self.job_repo.get_job(job_id)
        return ReindexJob.from_row(row) if row else None

    def get_latest_job(self) -> ReindexJob | None:
        row = self.job_repo.get_latest_job()
        return ReindexJob.from_row(row) if row else None

    def preview_target_runtime(self, settings: RAGSettings) -> EmbeddingRuntime:
        return self._build_runtime_from_settings(settings)

    def get_failed_chunk_category_counts(self, job_id: str) -> dict[str, int]:
        return self.job_repo.summarize_failed_chunks(job_id)

    def start_reindex(
        self,
        *,
        settings: RAGSettings,
        source_fingerprint: str,
        scope_type: str = "full",
        scope_ref: str = "",
        reason: str = "manual",
    ) -> ReindexJob:
        total_chunks = self._count_scope_chunks(scope_type=scope_type, scope_ref=scope_ref)
        if total_chunks <= 0:
            raise RuntimeError("No reindexable chunks found for the requested scope.")
        return self._start_job(
            settings=settings,
            source_fingerprint=source_fingerprint,
            scope_type=scope_type,
            scope_ref=scope_ref,
            reason=reason,
            total_chunks=total_chunks,
            metadata_extra=None,
        )

    def retry_failed_chunks(
        self,
        parent_job_id: str,
        *,
        reason: str = "retry_failed_chunks",
        reason_filter: str = "all",
    ) -> ReindexJob:
        parent = self.get_job(parent_job_id)
        if parent is None:
            raise RuntimeError("Parent reindex job was not found.")
        if parent.status != "completed_with_errors" or parent.failed_chunks <= 0:
            raise RuntimeError("Retry failed chunks is only available for completed_with_errors jobs with failed chunks.")

        failed_chunks = self.job_repo.get_failed_chunks(parent_job_id)
        if not failed_chunks:
            raise RuntimeError("No failed chunks were recorded for the selected job.")

        normalized_filter = (reason_filter or "all").strip().lower()
        if normalized_filter == "all":
            selected_chunks = failed_chunks
            retry_mode = "failed_chunks_only"
        else:
            selected_chunks = [
                item
                for item in failed_chunks
                if str(item.get("error_category", "") or "").strip().lower() == normalized_filter
            ]
            mode_map = {
                "embedding_provider_error": "failed_chunks_embedding_provider_error_only",
                "backend_error": "failed_chunks_backend_error_only",
            }
            retry_mode = mode_map.get(normalized_filter, "failed_chunks_filtered")

        chunk_ids = [str(item.get("chunk_id", "") or "").strip() for item in selected_chunks if str(item.get("chunk_id", "") or "").strip()]
        if not chunk_ids:
            if normalized_filter == "all":
                raise RuntimeError("No failed chunks were recorded for the selected job.")
            raise RuntimeError("No failed chunks match the selected retry reason filter.")

        settings = self._load_settings()
        settings.active_embedding_fingerprint = parent.target_fingerprint
        return self._start_job(
            settings=settings,
            source_fingerprint=parent.source_fingerprint or parent.target_fingerprint,
            scope_type="retry_failed_chunks",
            scope_ref=parent_job_id,
            reason=reason,
            total_chunks=len(chunk_ids),
            metadata_extra={
                "parent_job_id": parent_job_id,
                "retry_source_job_id": parent_job_id,
                "retry_mode": retry_mode,
                "retry_reason_filter": normalized_filter,
                "chunk_ids": chunk_ids,
                "target_fingerprint_override": parent.target_fingerprint,
                "target_provider_override": parent.target_provider,
                "target_model_override": parent.target_model,
                "target_backend_override": parent.backend_type,
                "target_collection_name_override": parent.output_collection_name,
                "target_vector_dim_override": parent.output_vector_dim,
                "parent_completion_reason": parent.completion_reason,
            },
        )

    def request_cancel(self, job_id: str, *, reason: str = "user_request") -> bool:
        job = self.get_job(job_id)
        if job is None or job.status not in {"queued", "running", "cancel_requested"}:
            return False
        self.job_repo.request_cancel(job_id, reason=reason)
        logger.info("reindex_job_cancel_requested job_id=%s reason=%s", job_id, reason)
        return True

    def promote_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job is None:
            return {"ok": False, "reason": "not_found", "job_id": job_id}
        if job.promotion_status != "eligible":
            return {"ok": False, "reason": "not_eligible", "job_id": job_id}
        if job.status not in {"completed", "completed_with_errors"}:
            return {"ok": False, "reason": "not_completed", "job_id": job_id}
        settings = self._load_settings()
        settings.active_embedding_fingerprint = job.target_fingerprint
        self._save_settings(settings)
        self.job_repo.mark_promoted(job_id)
        logger.info(
            "active_collection_switched fingerprint=%s collection=%s provider=%s model=%s",
            job.target_fingerprint,
            job.output_collection_name,
            job.target_provider,
            job.target_model,
        )
        return {
            "ok": True,
            "reason": "",
            "job_id": job_id,
            "fingerprint": job.target_fingerprint,
            "collection_name": job.output_collection_name,
        }

    def switch_active_fingerprint(self, fingerprint: str) -> bool:
        settings = self._load_settings()
        target = fingerprint.strip()
        if not target:
            return False
        settings.active_embedding_fingerprint = target
        self._save_settings(settings)
        descriptor = self._get_runtime_descriptor(target)
        logger.info(
            "active_collection_switched fingerprint=%s collection=%s provider=%s model=%s",
            target,
            str(descriptor.get("collection_name", "") if descriptor else ""),
            str(descriptor.get("provider_name", "") if descriptor else ""),
            str(descriptor.get("model_id", "") if descriptor else ""),
        )
        return True

    def maybe_schedule_for_settings_change(self, previous: RAGSettings, current: RAGSettings) -> ReindexJob | None:
        if not current.reindex_enabled:
            return None
        if self.document_repo.count_reindexable_chunks() <= 0:
            return None
        fingerprint_keys = (
            "embedding_provider",
            "embedding_model",
            "embedding_base_url",
            "embedding_model_path",
            "embedding_backend",
            "embedding_device",
        )
        previous_data = previous.to_dict()
        current_data = current.to_dict()
        if not any(previous_data.get(key) != current_data.get(key) for key in fingerprint_keys):
            return None
        source_fingerprint = previous.active_embedding_fingerprint.strip() or self._runtime_fingerprint_from_settings(previous)
        return self.start_reindex(
            settings=current,
            source_fingerprint=source_fingerprint,
            scope_type="full",
            reason="settings_changed",
        )

    def validate_reindex_output(
        self,
        *,
        job_id: str,
        vector_store: BaseVectorStore,
        runtime: EmbeddingRuntime,
        total_chunks: int,
        processed_chunks: int,
        succeeded_chunks: int,
        failed_chunks: int,
        sample_chunk_ids: list[str],
    ) -> dict[str, Any]:
        warnings: list[str] = []
        reasons: list[str] = []
        processed_ratio = 0.0 if total_chunks <= 0 else processed_chunks / max(1, total_chunks)
        failed_ratio = 0.0 if total_chunks <= 0 else failed_chunks / max(1, total_chunks)

        collection_readable = False
        vector_dim_match = runtime.dimension > 0 and vector_store.embedding_dim == runtime.dimension
        sample_lookup_passed = False
        query_smoke_test_passed = False

        try:
            collection_count = vector_store.count_vectors()
            collection_readable = collection_count >= max(1, succeeded_chunks)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"collection_read_failed:{exc}")
            collection_count = 0

        try:
            sample_present = vector_store.fetch_chunk_ids(sample_chunk_ids[:3]) if sample_chunk_ids else set()
            sample_lookup_passed = len(sample_present) >= min(len(sample_chunk_ids[:3]), 1)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"sample_lookup_failed:{exc}")

        try:
            smoke_query = ""
            if sample_chunk_ids:
                with self.db.connect() as conn:
                    row = conn.execute(
                        "SELECT content FROM document_chunks WHERE id = ? LIMIT 1",
                        (sample_chunk_ids[0],),
                    ).fetchone()
                smoke_query = str(row["content"] or "")[:120] if row else ""
            if smoke_query:
                query_smoke_test_passed = bool(vector_store.similarity_search(smoke_query, top_k=1))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"query_smoke_failed:{exc}")

        if processed_ratio < self.HEALTH_MIN_PROCESSED_RATIO:
            reasons.append("processed_ratio_below_threshold")
        if failed_ratio > self.HEALTH_MAX_FAILED_RATIO:
            reasons.append("failed_ratio_above_threshold")
        if not collection_readable:
            reasons.append("collection_not_readable")
        if not vector_dim_match:
            reasons.append("vector_dim_mismatch")
        if not sample_lookup_passed:
            reasons.append("sample_lookup_failed")
        if not query_smoke_test_passed:
            reasons.append("query_smoke_test_failed")

        passed = not reasons
        return {
            "processed_ratio": round(processed_ratio, 4),
            "failed_ratio": round(failed_ratio, 4),
            "collection_readable": collection_readable,
            "vector_dim_match": vector_dim_match,
            "sample_lookup_passed": sample_lookup_passed,
            "query_smoke_test_passed": query_smoke_test_passed,
            "warnings": warnings,
            "reasons": reasons,
            "passed": passed,
            "collection_count": collection_count,
            "backend_type": vector_store.backend_name,
            "output_collection_name": runtime.collection_name,
            "output_vector_dim": runtime.dimension,
            "output_embedding_fingerprint": runtime.fingerprint,
            "job_id": job_id,
        }

    def _start_job(
        self,
        *,
        settings: RAGSettings,
        source_fingerprint: str,
        scope_type: str,
        scope_ref: str,
        reason: str,
        total_chunks: int,
        metadata_extra: dict[str, Any] | None,
    ) -> ReindexJob:
        if not settings.reindex_enabled:
            raise RuntimeError("Reindex is disabled in settings.")

        target_runtime = self._build_runtime_from_settings(settings)
        if metadata_extra:
            override_fingerprint = str(metadata_extra.get("target_fingerprint_override", "") or "").strip()
            override_provider = str(metadata_extra.get("target_provider_override", "") or "").strip()
            override_model = str(metadata_extra.get("target_model_override", "") or "").strip()
            override_collection = str(metadata_extra.get("target_collection_name_override", "") or "").strip()
            override_dim = int(metadata_extra.get("target_vector_dim_override", 0) or 0)
            if override_fingerprint:
                target_runtime.fingerprint = override_fingerprint
            if override_provider:
                target_runtime.provider_name = override_provider
            if override_model:
                target_runtime.model_id = override_model
            if override_collection:
                target_runtime.collection_name = override_collection
            if override_dim > 0:
                target_runtime.dimension = override_dim

        latest = self.get_latest_job()
        is_retry_job = bool(metadata_extra and metadata_extra.get("retry_mode"))
        if (
            not is_retry_job
            and latest is not None
            and latest.status in {"queued", "running", "cancel_requested"}
            and latest.target_fingerprint == target_runtime.fingerprint
        ):
            return latest

        self._register_runtime_descriptor(settings, target_runtime)
        metadata = {
            "reason": reason,
            "scope_type": scope_type,
            "scope_ref": scope_ref,
            "target_settings": settings.to_dict(),
            "target_collection_name": target_runtime.collection_name,
            "target_provider": target_runtime.provider_name,
            "target_model": target_runtime.model_id,
        }
        if metadata_extra:
            metadata.update(metadata_extra)

        row = self.job_repo.create_job(
            source_fingerprint=source_fingerprint,
            target_fingerprint=target_runtime.fingerprint,
            target_provider=target_runtime.provider_name,
            target_model=target_runtime.model_id,
            scope_type=scope_type,
            scope_ref=scope_ref,
            total_chunks=total_chunks,
            status="queued",
            promotion_status="not_ready",
            completion_reason="",
            metadata=metadata,
        )
        job = ReindexJob.from_row(row)
        logger.info(
            "reindex_job_created job_id=%s source=%s target=%s provider=%s model=%s total=%s",
            job.job_id,
            source_fingerprint,
            target_runtime.fingerprint,
            target_runtime.provider_name,
            target_runtime.model_id,
            total_chunks,
        )
        worker = threading.Thread(
            target=self._run_job,
            args=(job.job_id, settings.to_dict(), source_fingerprint),
            name=f"reindex-{job.job_id}",
            daemon=True,
        )
        with self._lock:
            self._threads[job.job_id] = worker
        worker.start()
        return job

    def _run_job(self, job_id: str, target_settings_dict: dict[str, Any], source_fingerprint: str) -> None:
        settings = RAGSettings.from_dict(target_settings_dict)
        warning_count = 0
        sample_chunk_ids: list[str] = []
        job = self.get_job(job_id)
        if job is None:
            self._remove_thread(job_id)
            return

        target_backend_override = str(job.metadata.get("target_backend_override", "") or "").strip()
        if target_backend_override:
            settings.vector_backend = target_backend_override

        chunk_ids = [str(item).strip() for item in list(job.metadata.get("chunk_ids") or []) if str(item).strip()]
        self.job_repo.clear_failed_chunks(job_id)

        try:
            runtime = self._build_runtime_from_settings(settings)
            if job.target_fingerprint:
                runtime.fingerprint = job.target_fingerprint
            if job.target_provider:
                runtime.provider_name = job.target_provider
            if job.target_model:
                runtime.model_id = job.target_model
            override_collection = str(job.metadata.get("target_collection_name", "") or job.output_collection_name or "").strip()
            override_dim = int(job.metadata.get("target_vector_dim_override", 0) or job.output_vector_dim or 0)
            if override_collection:
                runtime.collection_name = override_collection
            if override_dim > 0:
                runtime.dimension = override_dim
        except Exception as exc:  # noqa: BLE001
            logger.exception("reindex_job_failed job_id=%s reason=embedding_provider_error", job_id)
            self.job_repo.mark_failed(job_id, completion_reason="embedding_provider_error", last_error=str(exc))
            self.job_repo.set_promotion_status(job_id, status="rejected", completion_reason="embedding_provider_error")
            self._remove_thread(job_id)
            return

        try:
            vector_store = self._create_vector_store(settings, runtime)
        except Exception as exc:  # noqa: BLE001
            logger.exception("reindex_job_failed job_id=%s reason=backend_unavailable", job_id)
            self.job_repo.mark_failed(job_id, completion_reason="backend_unavailable", last_error=str(exc))
            self.job_repo.set_promotion_status(job_id, status="rejected", completion_reason="backend_unavailable")
            self._remove_thread(job_id)
            return

        try:
            self.job_repo.mark_running(
                job_id,
                backend_type=vector_store.backend_name,
                output_collection_name=runtime.collection_name,
                output_vector_dim=runtime.dimension,
                output_embedding_fingerprint=runtime.fingerprint,
            )
            logger.info(
                "reindex_job_started job_id=%s source=%s target=%s backend=%s collection=%s",
                job_id,
                source_fingerprint,
                runtime.fingerprint,
                vector_store.backend_name,
                runtime.collection_name,
            )

            batch_size = max(1, settings.reindex_batch_size)
            offset = 0
            while True:
                if self.job_repo.is_cancel_requested(job_id):
                    self.job_repo.mark_cancelled(job_id, cancel_reason="user_request", completion_reason="cancelled_by_user")
                    logger.info("reindex_job_cancelled job_id=%s", job_id)
                    return

                rows: list[dict[str, Any]]
                missing_ids: list[str] = []
                if chunk_ids:
                    batch_ids = chunk_ids[offset : offset + batch_size]
                    if not batch_ids:
                        break
                    rows = self.document_repo.list_chunks_by_ids(batch_ids)
                    offset += len(batch_ids)
                    returned_ids = {str(row.get("id", "") or "") for row in rows}
                    missing_ids = [chunk_id for chunk_id in batch_ids if chunk_id not in returned_ids]
                else:
                    rows = self.document_repo.list_reindexable_chunks(
                        offset=offset,
                        limit=batch_size,
                        **self._scope_filters(scope_type=job.scope_type, scope_ref=job.scope_ref),
                    )
                    if not rows:
                        break
                    offset += len(rows)

                if missing_ids:
                    warning_count += len(missing_ids)
                    self.job_repo.increment_progress(
                        job_id,
                        processed=len(missing_ids),
                        skipped=len(missing_ids),
                        warning_increment=len(missing_ids),
                        last_error="missing_chunk_rows",
                    )

                chunks: list[ChunkRecord] = []
                skipped_in_batch = 0
                for row in rows:
                    content = str(row.get("content", "") or "").strip()
                    if not content:
                        skipped_in_batch += 1
                        continue
                    chunks.append(self._row_to_chunk_record(row, runtime=runtime))
                    if len(sample_chunk_ids) < 5:
                        sample_chunk_ids.append(str(row.get("id", "") or ""))

                if skipped_in_batch:
                    warning_count += skipped_in_batch
                    self.job_repo.increment_progress(
                        job_id,
                        processed=skipped_in_batch,
                        skipped=skipped_in_batch,
                        warning_increment=skipped_in_batch,
                    )

                if not chunks:
                    continue

                try:
                    self._upsert_batch(vector_store, chunks)
                    self.job_repo.increment_progress(job_id, processed=len(chunks), succeeded=len(chunks))
                    logger.info(
                        "reindex_chunk_processed job_id=%s processed=%s total=%s fingerprint=%s",
                        job_id,
                        self.get_job(job_id).processed_chunks if self.get_job(job_id) else 0,
                        job.total_chunks,
                        runtime.fingerprint,
                    )
                except Exception as batch_exc:  # noqa: BLE001
                    warning_count += 1
                    logger.warning("reindex_batch_failed job_id=%s error=%s", job_id, batch_exc)
                    for chunk in chunks:
                        if self.job_repo.is_cancel_requested(job_id):
                            self.job_repo.mark_cancelled(
                                job_id,
                                cancel_reason="user_request",
                                completion_reason="cancelled_by_user",
                                last_error=str(batch_exc),
                            )
                            logger.info("reindex_job_cancelled job_id=%s after_partial_batch", job_id)
                            return
                        try:
                            self._upsert_batch(vector_store, [chunk])
                            self.job_repo.increment_progress(job_id, processed=1, succeeded=1)
                        except Exception as chunk_exc:  # noqa: BLE001
                            self.job_repo.increment_progress(
                                job_id,
                                processed=1,
                                failed=1,
                                warning_increment=1,
                                last_error=str(chunk_exc),
                            )
                            self.job_repo.add_failed_chunk(
                                job_id,
                                chunk_id=chunk.id,
                                error_category=self._categorize_failed_chunk_error(chunk_exc),
                                error_message=str(chunk_exc),
                                metadata={
                                    "source_ref_id": chunk.metadata.source_ref_id,
                                    "source_kind": chunk.metadata.source_kind,
                                    "title": chunk.metadata.title,
                                },
                            )
                            logger.warning(
                                "reindex_chunk_failed job_id=%s chunk_id=%s error=%s",
                                job_id,
                                chunk.id,
                                chunk_exc,
                            )

            final_job = self.get_job(job_id)
            if final_job is None:
                return

            health_summary = self.validate_reindex_output(
                job_id=job_id,
                vector_store=vector_store,
                runtime=runtime,
                total_chunks=final_job.total_chunks,
                processed_chunks=final_job.processed_chunks,
                succeeded_chunks=final_job.succeeded_chunks,
                failed_chunks=final_job.failed_chunks,
                sample_chunk_ids=sample_chunk_ids,
            )
            self.job_repo.set_health_check_result(job_id, passed=bool(health_summary.get("passed", False)), summary=health_summary)

            completion_reason = "success" if final_job.failed_chunks <= 0 and warning_count <= 0 else "partial_success"
            if bool(health_summary.get("passed", False)):
                self.job_repo.set_promotion_status(job_id, status="eligible")
                if final_job.failed_chunks > 0 or warning_count > 0:
                    self.job_repo.mark_completed_with_errors(
                        job_id,
                        completion_reason=completion_reason,
                        last_error=str(final_job.last_error or ""),
                    )
                else:
                    self.job_repo.mark_completed(job_id, completion_reason=completion_reason)
            else:
                self.job_repo.set_promotion_status(job_id, status="rejected", completion_reason="validation_failed")
                last_error = self._summarize_health_reasons(health_summary)
                if final_job.failed_chunks > 0 or warning_count > 0:
                    self.job_repo.mark_completed_with_errors(
                        job_id,
                        completion_reason="validation_failed",
                        last_error=last_error,
                    )
                else:
                    self.job_repo.mark_failed(
                        job_id,
                        completion_reason="validation_failed",
                        last_error=last_error,
                    )

            stored_job = self.get_job(job_id)
            if stored_job is not None:
                logger.info(
                    "reindex_job_completed job_id=%s status=%s promotion=%s processed=%s succeeded=%s failed=%s skipped=%s backend=%s",
                    job_id,
                    stored_job.status,
                    stored_job.promotion_status,
                    stored_job.processed_chunks,
                    stored_job.succeeded_chunks,
                    stored_job.failed_chunks,
                    stored_job.skipped_chunks,
                    stored_job.backend_type,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("reindex_job_failed job_id=%s", job_id)
            self.job_repo.mark_failed(job_id, completion_reason="worker_exception", last_error=str(exc))
            self.job_repo.set_promotion_status(job_id, status="rejected", completion_reason="worker_exception")
        finally:
            self._remove_thread(job_id)

    def _summarize_health_reasons(self, summary: dict[str, Any]) -> str:
        reasons = list(summary.get("reasons") or [])
        warnings = list(summary.get("warnings") or [])
        parts = reasons[:4] + warnings[:2]
        return "; ".join(str(item) for item in parts if str(item).strip())[:1000]

    def _categorize_failed_chunk_error(self, exc: Exception) -> str:
        message = str(exc).strip().lower()
        if any(token in message for token in ("embedding", "provider", "api", "auth", "timeout", "rate limit")):
            return "embedding_provider_error"
        if any(token in message for token in ("backend", "sqlite", "database", "chroma", "collection", "disk")):
            return "backend_error"
        return "unknown"

    def _create_vector_store(self, settings: RAGSettings, runtime: EmbeddingRuntime) -> BaseVectorStore:
        return build_vector_store(settings=settings, embedding_runtime=runtime, db=self.db)

    def _upsert_batch(self, vector_store: BaseVectorStore, chunks: list[ChunkRecord]) -> None:
        vector_store.upsert_chunks(chunks, update_chunk_metadata=False)

    def _row_to_chunk_record(self, row: dict[str, Any], *, runtime: EmbeddingRuntime) -> ChunkRecord:
        try:
            metadata_payload = json.loads(str(row.get("metadata_json") or "{}"))
        except Exception:
            metadata_payload = {}
        metadata = ChunkMetadata(
            source_kind=str(metadata_payload.get("source_kind") or row.get("source_kind") or ""),
            source_ref_id=str(metadata_payload.get("source_ref_id") or row.get("source_ref_id") or ""),
            title=str(metadata_payload.get("title", "") or ""),
            tags=list(metadata_payload.get("tags") or []),
            created_at=str(metadata_payload.get("created_at") or row.get("created_at") or ""),
            session_id=str(metadata_payload.get("session_id", "") or ""),
            importance=float(metadata_payload.get("importance", 0.0) or 0.0),
            document_id=str(metadata_payload.get("document_id") or row.get("document_id") or ""),
            embedding_provider=runtime.provider_name,
            embedding_model_id=runtime.model_id,
            embedding_dim=int(runtime.dimension),
            embedding_fingerprint=runtime.fingerprint,
        )
        return ChunkRecord(
            id=str(row.get("id", "") or ""),
            content=str(row.get("content", "") or ""),
            chunk_index=int(row.get("chunk_index", 0) or 0),
            token_estimate=int(row.get("token_estimate", 0) or 0),
            metadata=metadata,
        )

    def _scope_filters(self, *, scope_type: str, scope_ref: str) -> dict[str, str]:
        if scope_type == "document_id" and scope_ref:
            return {"document_id": scope_ref}
        if scope_type == "source_kind" and scope_ref:
            return {"source_kind": scope_ref}
        if scope_type == "session" and scope_ref:
            return {"source_ref_id": scope_ref}
        return {}

    def _count_scope_chunks(self, *, scope_type: str, scope_ref: str) -> int:
        return self.document_repo.count_reindexable_chunks(**self._scope_filters(scope_type=scope_type, scope_ref=scope_ref))

    def _build_runtime_from_settings(self, settings: RAGSettings) -> EmbeddingRuntime:
        runtime = self.embedding_factory.create(settings)
        provider = runtime.provider_name.strip().lower() or "hash"
        model_id = runtime.model_id.strip() or provider
        dimension = runtime.dimension
        if dimension <= 0:
            sample = runtime.service.embed_texts(["fairy embedding probe"])
            dimension = len(sample[0]) if sample else int(getattr(runtime.service, "dimension_hint", 0) or 0)
        fingerprint = self._build_embedding_fingerprint(provider=provider, model_id=model_id, dimension=dimension)
        collection_base = self._sanitize_slug(settings.chroma_collection_name.strip() or "fairy_knowledge")
        runtime.dimension = dimension
        runtime.fingerprint = fingerprint
        runtime.collection_name = f"{collection_base}_{fingerprint}"
        return runtime

    def _register_runtime_descriptor(self, settings: RAGSettings, runtime: EmbeddingRuntime) -> None:
        registry = self.settings_repo.get_json(self.RUNTIME_REGISTRY_KEY, {}) or {}
        registry[runtime.fingerprint] = {
            "provider_name": runtime.provider_name,
            "model_id": runtime.model_id,
            "dimension": runtime.dimension,
            "fingerprint": runtime.fingerprint,
            "collection_name": runtime.collection_name,
            "settings": settings.to_dict(),
        }
        self.settings_repo.set_json(self.RUNTIME_REGISTRY_KEY, registry)

    def _get_runtime_descriptor(self, fingerprint: str) -> dict[str, Any] | None:
        registry = self.settings_repo.get_json(self.RUNTIME_REGISTRY_KEY, {}) or {}
        descriptor = registry.get(fingerprint)
        return descriptor if isinstance(descriptor, dict) else None

    def _runtime_fingerprint_from_settings(self, settings: RAGSettings) -> str:
        runtime = self._build_runtime_from_settings(settings)
        self._register_runtime_descriptor(settings, runtime)
        return runtime.fingerprint

    def _load_settings(self) -> RAGSettings:
        return RAGSettings.from_dict(self.settings_repo.get_json(self.RAG_SETTINGS_KEY))

    def _save_settings(self, settings: RAGSettings) -> None:
        self.settings_repo.set_json(self.RAG_SETTINGS_KEY, settings.to_dict())
        try:
            from app.rag.rag_manager import get_rag_manager

            get_rag_manager().invalidate_runtime()
        except Exception:
            logger.debug("Failed to invalidate rag runtime after active collection switch.", exc_info=True)

    def _build_embedding_fingerprint(self, *, provider: str, model_id: str, dimension: int) -> str:
        if provider == "hash":
            return "hash-v1"
        prefix_map = {"qwen_cloud": "qwen3", "bge_local": "bge"}
        prefix = prefix_map.get(provider, self._sanitize_slug(provider))
        return f"{prefix}-{self._sanitize_slug(model_id.replace('/', '-'))}-{dimension}"

    def _sanitize_slug(self, value: str) -> str:
        import re

        slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
        return slug or "default"

    def _remove_thread(self, job_id: str) -> None:
        with self._lock:
            self._threads.pop(job_id, None)


_REINDEX_MANAGER: ReindexManager | None = None


def get_reindex_manager() -> ReindexManager:
    global _REINDEX_MANAGER
    if _REINDEX_MANAGER is None:
        _REINDEX_MANAGER = ReindexManager()
    return _REINDEX_MANAGER
