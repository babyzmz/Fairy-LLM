from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.rag.chunker import TextChunker
from app.rag.dedup import fingerprint_text
from app.rag.embedding_service import EmbeddingRuntime, EmbeddingServiceFactory, HashEmbeddingService
from app.rag.importance_scorer import ImportanceScorer, KnowledgeScoreDetails
from app.rag.ingestion_pipeline import IngestionPipeline
from app.rag.rag_schema import RAGSettings, RetrievalResult
from app.rag.retriever import RAGRetriever
from app.rag.session_aggregator import SessionAggregator
from app.rag.vector_store import BaseVectorStore, build_vector_store
from app.storage.db import AppDatabase, get_app_database
from app.storage.repositories.document_repo import DocumentRepo
from app.storage.repositories.memory_repo import MemoryRepo
from app.storage.repositories.message_repo import MessageRepo
from app.storage.repositories.session_repo import SessionRepo
from app.storage.repositories.settings_repo import SettingsRepo


logger = logging.getLogger(__name__)


class RagManager:
    SETTINGS_KEY = "rag_settings"
    RUNTIME_REGISTRY_KEY = "rag_embedding_runtime_registry"

    def __init__(self, db: AppDatabase | None = None) -> None:
        self.db = db or get_app_database()
        self.settings_repo = SettingsRepo(self.db)
        self.memory_repo = MemoryRepo(self.db)
        self.document_repo = DocumentRepo(self.db)
        self.message_repo = MessageRepo(self.db)
        self.session_repo = SessionRepo(self.db)
        self.chunker = TextChunker()
        self.embedding_factory = EmbeddingServiceFactory()
        self.importance_scorer = ImportanceScorer()
        self.session_aggregator = SessionAggregator()
        self._runtime_settings_signature = ""
        self._embedding_runtime: EmbeddingRuntime | None = None
        self._vector_store: BaseVectorStore | None = None
        self._retriever: RAGRetriever | None = None
        self._ingestion: IngestionPipeline | None = None

    def load_settings(self) -> RAGSettings:
        return RAGSettings.from_dict(self.settings_repo.get_json(self.SETTINGS_KEY))

    def save_settings(self, settings: RAGSettings) -> None:
        previous = self.load_settings()
        self.settings_repo.set_json(self.SETTINGS_KEY, settings.to_dict())
        self.invalidate_runtime()
        try:
            from app.rag.reindex_manager import ReindexManager

            ReindexManager(self.db).maybe_schedule_for_settings_change(previous, settings)
        except Exception:
            logger.exception("Failed to evaluate reindex scheduling after rag settings update.")

    def invalidate_runtime(self) -> None:
        self._runtime_settings_signature = ""

    def should_use_rag(
        self,
        user_query: str,
        *,
        mode: str,
        task_type: str,
        settings: RAGSettings | None = None,
        attachment_paths: list[str] | None = None,
    ) -> bool:
        settings = settings or self.load_settings()
        if not settings.rag_enabled or mode == "game_mode":
            return False

        lowered = user_query.lower()
        history_markers = (
            "之前",
            "以前",
            "讨论过",
            "历史",
            "方案",
            "怎么定的",
            "怎么说来着",
            "game mode",
            "persona",
            "memory",
            "rag",
        )
        document_markers = (
            "文档",
            "附件",
            "markdown",
            "md",
            "json",
            "csv",
            "yaml",
            "py",
            "文件",
            "根据这份",
        )
        if settings.rag_use_for_history_queries and any(marker in lowered for marker in history_markers):
            return True
        if settings.rag_use_for_document_qa and (
            any(marker in lowered for marker in document_markers) or bool(list(attachment_paths or []))
        ):
            return True
        if task_type in {"document_read", "coding_help"} and settings.rag_use_for_document_qa:
            return True
        return False

    def retrieve_context(
        self,
        query: str,
        *,
        top_k: int | None = None,
        settings: RAGSettings | None = None,
        filters: dict[str, Any] | None = None,
    ) -> RetrievalResult:
        settings = settings or self.load_settings()
        effective_top_k = top_k or settings.rag_top_k
        if not settings.rag_enabled:
            return RetrievalResult(
                query=query,
                debug_snapshot={
                    "rag_enabled": False,
                    "triggered": False,
                    "query": query,
                },
            )

        self._ensure_runtime(settings)
        assert self._retriever is not None
        try:
            hits = self._retriever.search(query, top_k=effective_top_k, filters=filters)
        except Exception as exc:  # noqa: BLE001
            if self.embedding_provider_name != "hash":
                self._fallback_runtime(settings, f"retrieve_failed:{exc}")
                assert self._retriever is not None
                hits = self._retriever.search(query, top_k=effective_top_k, filters=filters)
            else:
                raise

        context_meta = self._build_context_block(hits, max_chars=settings.rag_max_context_chars)
        prompt_block = str(context_meta.get("prompt_block", "") or "")
        source_kinds = sorted({item.source_kind for item in hits})
        debug_snapshot = self._build_debug_snapshot(
            query=query,
            hits=hits,
            top_k=effective_top_k,
            prompt_block=prompt_block,
            context_meta=context_meta,
            source_kinds=source_kinds,
            injected=bool(prompt_block),
            max_context_chars=settings.rag_max_context_chars,
        )
        logger.info(
            "rag_retrieval provider=%s fingerprint=%s backend=%s hits=%s top_k=%s injected_chars=%s",
            self.embedding_provider_name,
            self.embedding_fingerprint,
            self.vector_backend_name,
            len(hits),
            effective_top_k,
            len(prompt_block),
        )
        return RetrievalResult(query=query, chunks=hits, prompt_block=prompt_block, debug_snapshot=debug_snapshot)

    @property
    def embedding_provider_name(self) -> str:
        return self._embedding_runtime.provider_name if self._embedding_runtime is not None else "unknown"

    @property
    def embedding_model_id(self) -> str:
        return self._embedding_runtime.model_id if self._embedding_runtime is not None else ""

    @property
    def embedding_fingerprint(self) -> str:
        return self._embedding_runtime.fingerprint if self._embedding_runtime is not None else ""

    @property
    def embedding_collection_name(self) -> str:
        return self._embedding_runtime.collection_name if self._embedding_runtime is not None else ""

    @property
    def embedding_fallback_used(self) -> bool:
        return bool(self._embedding_runtime and self._embedding_runtime.fallback_used)

    @property
    def embedding_fallback_reason(self) -> str:
        return self._embedding_runtime.fallback_reason if self._embedding_runtime is not None else ""

    @property
    def vector_backend_name(self) -> str:
        return self._vector_store.backend_name if self._vector_store is not None else "unknown"

    def save_session_summary(
        self,
        *,
        title: str,
        content: str,
        session_id: str | None = None,
        tags: list[str] | None = None,
        importance: float = 0.65,
        settings: RAGSettings | None = None,
    ) -> str:
        settings = settings or self.load_settings()
        self._ensure_runtime(settings)
        return self._persist_memory_card(
            memory_type="session_summary",
            source_kind="message_summary",
            title=title,
            content=content,
            session_id=session_id,
            source_message_ids=None,
            tags=tags or ["session_summary"],
            importance=importance,
            settings=settings,
            decision_status="confirmed",
            vectorize=True,
        )[0]

    def save_decision_card(
        self,
        *,
        title: str,
        content: str,
        session_id: str | None = None,
        source_message_ids: list[str] | None = None,
        tags: list[str] | None = None,
        importance: float = 0.88,
        settings: RAGSettings | None = None,
    ) -> str:
        settings = settings or self.load_settings()
        self._ensure_runtime(settings)
        return self._persist_memory_card(
            memory_type="decision_card",
            source_kind="decision_card",
            title=title,
            content=content,
            session_id=session_id,
            source_message_ids=source_message_ids,
            tags=tags or ["decision_card"],
            importance=importance,
            settings=settings,
            decision_status="confirmed",
            vectorize=True,
        )[0]

    def list_pending_decisions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        items = self.memory_repo.list_pending_decisions(limit=limit)
        normalized: list[dict[str, Any]] = []
        for item in items:
            normalized.append(
                {
                    **item,
                    "tags": self._json_list(item.get("tags_json")),
                    "source_message_ids": self._json_list(item.get("source_message_ids_json")),
                    "merged_source_refs": self._json_list(item.get("merged_source_refs_json")),
                }
            )
        return normalized

    def confirm_decision_candidate(self, item_id: str) -> dict[str, Any]:
        settings = self.load_settings()
        self._ensure_runtime(settings)
        item = self.memory_repo.get_item(item_id)
        if item is None:
            return {"ok": False, "reason": "not_found", "item_id": item_id}
        if str(item.get("memory_type", "")) != "decision_card":
            return {"ok": False, "reason": "not_decision_card", "item_id": item_id}

        title = str(item.get("title", "") or "").strip()
        content = str(item.get("content", "") or "").strip()
        session_id = str(item.get("source_session_id", "") or "").strip()
        tags = self._json_list(item.get("tags_json")) or ["decision_card"]
        canonical_key = str(item.get("canonical_key", "") or "").strip() or self._build_canonical_key(
            memory_type="decision_card",
            title=title,
            content=content,
        )

        existing = self.memory_repo.find_by_canonical_key("decision_card", canonical_key)
        if existing is not None and str(existing.get("id", "")) != item_id and str(existing.get("decision_status", "")) == "confirmed":
            existing_id = str(existing["id"])
            self.memory_repo.merge_into_existing(
                item_id=existing_id,
                content=content,
                importance=float(item.get("importance", 0.88) or 0.88),
                source_session_id=session_id or None,
                source_message_ids=self._json_list(item.get("source_message_ids_json")),
                merged_source_refs=[canonical_key],
            )
            self.memory_repo.update_decision_status(item_id, "rejected")
            logger.info("decision_confirmed merged_into=%s candidate=%s reason=canonical_key", existing_id, item_id)
            return {"ok": True, "merged": True, "item_id": existing_id, "reason": "canonical_key"}

        near_hit = self._find_near_duplicate(
            content,
            source_kind="decision_card",
            settings=settings,
            exclude_source_ref_id=item_id,
        )
        if near_hit is not None:
            existing_id = near_hit["item_id"]
            self.memory_repo.merge_into_existing(
                item_id=existing_id,
                content=content,
                importance=float(item.get("importance", 0.88) or 0.88),
                source_session_id=session_id or None,
                source_message_ids=self._json_list(item.get("source_message_ids_json")),
                merged_source_refs=[canonical_key],
                append_line=f"补充证据：{self._truncate_title(content, limit=180)}",
            )
            self.memory_repo.update_decision_status(item_id, "rejected")
            logger.info("decision_confirmed merged_into=%s candidate=%s reason=semantic", existing_id, item_id)
            return {"ok": True, "merged": True, "item_id": existing_id, "reason": "semantic"}

        self.memory_repo.update_decision_status(item_id, "confirmed")
        self._replace_memory_chunks(
            source_kind="decision_card",
            source_ref_id=item_id,
            title=title,
            content=content,
            session_id=session_id,
            importance=float(item.get("importance", 0.88) or 0.88),
            tags=tags,
            settings=settings,
        )
        logger.info("decision_confirmed item_id=%s", item_id)
        return {"ok": True, "merged": False, "item_id": item_id, "reason": ""}

    def reject_decision_candidate(self, item_id: str) -> dict[str, Any]:
        item = self.memory_repo.get_item(item_id)
        if item is None:
            return {"ok": False, "reason": "not_found", "item_id": item_id}
        self.memory_repo.update_decision_status(item_id, "rejected")
        if self._vector_store is not None:
            try:
                self._vector_store.delete_by_source_ref_id(item_id)
            except Exception:
                logger.exception("Failed to remove vectorized decision candidate item_id=%s", item_id)
        logger.info("decision_rejected item_id=%s", item_id)
        return {"ok": True, "item_id": item_id}

    def promote_reindex_job(self, job_id: str) -> dict[str, Any]:
        from app.rag.reindex_manager import ReindexManager

        return ReindexManager(self.db).promote_job(job_id)

    def ingest_document(
        self,
        file_path: str | Path,
        *,
        title: str = "",
        source_type: str = "local_file",
        metadata: dict[str, Any] | None = None,
        settings: RAGSettings | None = None,
    ) -> str:
        settings = settings or self.load_settings()
        self._ensure_runtime(settings)
        assert self._ingestion is not None
        try:
            return self._ingestion.ingest_document(
                file_path,
                title=title,
                source_type=source_type,
                metadata=metadata or {},
            )
        except Exception as exc:  # noqa: BLE001
            if self.embedding_provider_name != "hash":
                self._fallback_runtime(settings, f"ingest_failed:{exc}")
                assert self._ingestion is not None
                return self._ingestion.ingest_document(
                    file_path,
                    title=title,
                    source_type=source_type,
                    metadata=metadata or {},
                )
            raise

    def maybe_store_task_knowledge(
        self,
        *,
        user_request: str,
        result_summary: str,
        response_text: str,
        recommendation: str,
        task_category: str,
        skill_name: str,
        session_id: str = "",
        changed_files: list[str] | None = None,
        commands_run: list[str] | None = None,
        attachment_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        settings = self.load_settings()
        self._ensure_runtime(settings)
        events: list[dict[str, Any]] = []
        if self.embedding_fallback_used:
            events.append(
                {
                    "name": "embedding_provider_fallback",
                    "payload": {
                        "provider": self.embedding_provider_name,
                        "reason": self.embedding_fallback_reason or "unknown",
                    },
                }
            )

        stats = {
            "session_summary": 0,
            "decision_card": 0,
            "pending_decision": 0,
            "documents": 0,
            "events": events,
            "embedding_provider": self.embedding_provider_name,
            "embedding_model_id": self.embedding_model_id,
            "embedding_fingerprint": self.embedding_fingerprint,
            "embedding_collection_name": self.embedding_collection_name,
            "vector_backend": self.vector_backend_name,
            "embedding_fallback_used": self.embedding_fallback_used,
            "embedding_fallback_reason": self.embedding_fallback_reason,
        }

        for file_path in attachment_paths or []:
            if not self._looks_like_text_document(file_path):
                continue
            try:
                self.ingest_document(
                    file_path,
                    title=Path(file_path).name,
                    metadata={"tags": ["imported_document"], "importance": 0.72},
                    settings=settings,
                )
                stats["documents"] += 1
            except Exception:
                logger.exception("Failed to ingest document for RAG path=%s", file_path)

        title = self._truncate_title(result_summary or user_request)
        content = self._build_candidate_content(
            user_request=user_request,
            result_summary=result_summary,
            response_text=response_text,
            recommendation=recommendation,
            skill_name=skill_name,
            task_category=task_category,
            changed_files=changed_files or [],
            commands_run=commands_run or [],
        )
        if not content.strip():
            return stats

        score_details = self.importance_scorer.score_knowledge_candidate(
            title=title,
            content=content,
            skill_name=skill_name,
            task_category=task_category,
        )
        events.append(
            {
                "name": "knowledge_candidate_scored",
                "payload": {
                    "title": title,
                    "score": score_details.total_score,
                    "reasons": list(score_details.reasons),
                    "skill_name": skill_name,
                },
            }
        )
        logger.info(
            "knowledge candidate score=%s reasons=%s title=%s",
            score_details.total_score,
            ",".join(score_details.reasons),
            title,
        )

        allow_decision_card = (
            self.importance_scorer.should_create_decision_card(score_details, settings.importance_threshold_memory)
            if settings.importance_gating_enabled
            else score_details.decision_score > 0
        )
        allow_summary = (
            self.importance_scorer.should_persist_as_memory(score_details, settings.importance_threshold_summary)
            if settings.importance_gating_enabled
            else True
        )

        if allow_decision_card:
            decision_title = self._truncate_title(self._build_decision_title(user_request, result_summary, skill_name))
            decision_content = self._build_decision_card(
                user_request=user_request,
                result_summary=result_summary,
                response_text=response_text,
                recommendation=recommendation,
                changed_files=changed_files or [],
                commands_run=commands_run or [],
            )
            _, persist_meta = self._persist_memory_card(
                memory_type="decision_card",
                source_kind="decision_card",
                title=decision_title,
                content=decision_content,
                session_id=session_id or None,
                tags=["decision_card", skill_name or "task"],
                importance=0.9,
                settings=settings,
                decision_status="pending",
                vectorize=False,
            )
            if persist_meta["dedup_hit"]:
                events.append(
                    {
                        "name": "knowledge_dedup_hit",
                        "payload": {
                            "existing_item_id": persist_meta["item_id"],
                            "memory_type": "decision_card",
                            "reason": persist_meta["dedup_reason"],
                        },
                    }
                )
            else:
                stats["pending_decision"] += 1
                events.append(
                    {
                        "name": "decision_candidate_created",
                        "payload": {
                            "item_id": persist_meta["item_id"],
                            "title": decision_title,
                            "score": score_details.total_score,
                        },
                    }
                )
                logger.info("decision_candidate_created item_id=%s title=%s", persist_meta["item_id"], decision_title)
        elif allow_summary:
            rollup_meta = self._maybe_persist_session_rollup(
                session_id=session_id,
                user_request=user_request,
                result_summary=result_summary,
                recommendation=recommendation,
                score_details=score_details,
                settings=settings,
            )
            if rollup_meta["created"]:
                stats["session_summary"] += 1
                events.append(
                    {
                        "name": "session_rollup_created",
                        "payload": {
                            "session_id": session_id,
                            "reason": rollup_meta["reason"],
                            "item_id": rollup_meta["item_id"],
                        },
                    }
                )
                if rollup_meta["dedup_hit"]:
                    events.append(
                        {
                            "name": "knowledge_dedup_hit",
                            "payload": {
                                "existing_item_id": rollup_meta["item_id"],
                                "memory_type": "session_summary",
                                "reason": rollup_meta["dedup_reason"],
                            },
                        }
                    )
            else:
                events.append(
                    {
                        "name": "knowledge_persist_skipped",
                        "payload": {
                            "reason": rollup_meta["reason"],
                            "score": score_details.total_score,
                        },
                    }
                )
        else:
            logger.info("knowledge skipped due to low importance score=%s", score_details.total_score)
            events.append(
                {
                    "name": "knowledge_persist_skipped",
                    "payload": {
                        "reason": "low_importance",
                        "score": score_details.total_score,
                    },
                }
            )
        return stats

    def handle_debug_command(self, user_request: str) -> dict[str, Any] | None:
        text = user_request.strip()
        if not text.lower().startswith("fairy rag"):
            return None
        parts = text.split(maxsplit=3)
        if len(parts) < 3:
            return {
                "ok": True,
                "response_text": "可用命令：fairy rag list | fairy rag pending | fairy rag search <query> | fairy rag ingest <path> | fairy rag settings",
            }
        command = parts[2].lower()
        if command == "list":
            items = self.memory_repo.list_recent(limit=12)
            if not items:
                return {"ok": True, "response_text": "当前还没有知识条目。"}
            lines = ["Recent knowledge:"]
            for item in items:
                lines.append(f"- {item.get('id', '')} [{item.get('memory_type', '')}] {str(item.get('title', '') or '')[:80]}")
            return {"ok": True, "response_text": "\n".join(lines)}
        if command == "pending":
            items = self.list_pending_decisions(limit=12)
            if not items:
                return {"ok": True, "response_text": "当前没有待确认的 decision card。"}
            lines = ["Pending decisions:"]
            for item in items:
                lines.append(f"- {item.get('id', '')} {str(item.get('title', '') or '')[:80]}")
            return {"ok": True, "response_text": "\n".join(lines)}
        if command == "search" and len(parts) >= 4:
            settings = self.load_settings()
            result = self.retrieve_context(parts[3].strip(), top_k=settings.rag_top_k, settings=settings)
            if not result.chunks:
                return {"ok": True, "response_text": "没有命中相关知识。"}
            lines = ["Retrieved Context:"]
            for chunk in result.chunks:
                lines.append(f"- [{chunk.source_kind}] {chunk.title or chunk.source_ref_id} score={chunk.score:.3f}")
            return {"ok": True, "response_text": "\n".join(lines)}
        if command == "ingest" and len(parts) >= 4:
            doc_id = self.ingest_document(parts[3].strip(), title=Path(parts[3].strip()).name)
            return {"ok": True, "response_text": f"文档已入库：{doc_id}"}
        if command == "settings":
            settings = self.load_settings()
            return {"ok": True, "response_text": str(settings.to_dict())}
        return {"ok": False, "response_text": "无法识别的 rag 命令。"}

    def _ensure_runtime(self, settings: RAGSettings) -> None:
        signature = repr(settings.to_dict())
        if signature == self._runtime_settings_signature and self._vector_store is not None and self._retriever is not None and self._ingestion is not None:
            return

        previous_provider = self.embedding_provider_name
        previous_fingerprint = self.embedding_fingerprint

        runtime = self.embedding_factory.create(settings)
        try:
            hydrated = self._hydrate_runtime(runtime, settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding_provider_fallback provider=%s reason=%s", runtime.provider_name, exc)
            hydrated = self._build_hash_runtime(settings, f"hydrate_failed:{exc}")

        self._embedding_runtime = hydrated
        self._vector_store = build_vector_store(settings=settings, embedding_runtime=hydrated, db=self.db)
        self._retriever = RAGRetriever(self._vector_store)
        self._ingestion = IngestionPipeline(
            memory_repo=self.memory_repo,
            document_repo=self.document_repo,
            vector_store=self._vector_store,
            chunker=self.chunker,
        )
        self._runtime_settings_signature = signature

        if previous_provider and (
            previous_provider != hydrated.provider_name or previous_fingerprint != hydrated.fingerprint
        ):
            logger.info(
                "embedding_provider_switched previous=%s/%s current=%s/%s",
                previous_provider,
                previous_fingerprint,
                hydrated.provider_name,
                hydrated.fingerprint,
            )

        if hydrated.fallback_used:
            logger.warning(
                "Embedding provider fallback engaged provider=%s reason=%s",
                hydrated.provider_name,
                hydrated.fallback_reason,
            )
        else:
            logger.info(
                "Embedding provider initialized provider=%s model=%s fingerprint=%s backend=%s",
                hydrated.provider_name,
                hydrated.model_id,
                hydrated.fingerprint,
                self.vector_backend_name,
            )

    def _hydrate_runtime(self, runtime: EmbeddingRuntime, settings: RAGSettings) -> EmbeddingRuntime:
        dimension = int(runtime.dimension or 0)
        if dimension <= 0:
            dimension = int(getattr(runtime.service, "dimension_hint", 0) or 0)
        if dimension <= 0:
            probe = runtime.service.embed_texts(["fairy embedding probe"])
            dimension = len(probe[0]) if probe and probe[0] else 0
        if dimension <= 0:
            raise ValueError("embedding dimension is unavailable")

        provider = runtime.provider_name.strip().lower() or "hash"
        model_id = runtime.model_id.strip() or getattr(runtime.service, "model_id", "") or provider
        fingerprint = self._build_embedding_fingerprint(provider=provider, model_id=model_id, dimension=dimension)
        collection_base = self._sanitize_slug(settings.chroma_collection_name.strip() or "fairy_knowledge")
        collection_name = f"{collection_base}_{fingerprint}"
        return EmbeddingRuntime(
            service=runtime.service,
            provider_name=provider,
            model_id=model_id,
            dimension=dimension,
            fingerprint=fingerprint,
            collection_name=collection_name,
            fallback_used=runtime.fallback_used,
            fallback_reason=runtime.fallback_reason,
        )

    def _build_hash_runtime(self, settings: RAGSettings, reason: str) -> EmbeddingRuntime:
        service = HashEmbeddingService()
        dimension = int(getattr(service, "dimension_hint", 192) or 192)
        fingerprint = "hash-v1"
        collection_base = self._sanitize_slug(settings.chroma_collection_name.strip() or "fairy_knowledge")
        return EmbeddingRuntime(
            service=service,
            provider_name="hash",
            model_id="hash-v1",
            dimension=dimension,
            fingerprint=fingerprint,
            collection_name=f"{collection_base}_{fingerprint}",
            fallback_used=True,
            fallback_reason=reason,
        )

    def _build_embedding_fingerprint(self, *, provider: str, model_id: str, dimension: int) -> str:
        if provider == "hash":
            return "hash-v1"
        prefix_map = {
            "qwen_cloud": "qwen3",
            "bge_local": "bge",
        }
        prefix = prefix_map.get(provider, self._sanitize_slug(provider))
        model_slug = self._sanitize_slug(model_id.replace("/", "-"))
        return f"{prefix}-{model_slug}-{dimension}"

    def _sanitize_slug(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
        return slug or "default"

    def _build_context_block(self, chunks: list[Any], *, max_chars: int) -> str:
        if not chunks:
            return ""
        lines = ["[Retrieved Context]"]
        used = len(lines[0]) + 1
        for index, chunk in enumerate(chunks, start=1):
            header = f"{index}. 来源: {chunk.source_kind} / {chunk.title or chunk.source_ref_id} / {chunk.created_at or 'unknown'}"
            content = chunk.content.strip()
            remaining = max_chars - used - len(header) - 16
            if remaining <= 0:
                break
            snippet = content[:remaining].strip()
            block = f"{header}\n   内容: {snippet}"
            lines.append(block)
            used += len(block) + 1
            if used >= max_chars:
                break
        return "\n".join(lines) if len(lines) > 1 else ""

    def _build_debug_snapshot(
        self,
        *,
        query: str,
        hits: list[Any],
        top_k: int,
        prompt_block: str,
        source_kinds: list[str],
        injected: bool,
    ) -> dict[str, Any]:
        return {
            "rag_enabled": True,
            "triggered": True,
            "query": query,
            "top_k": top_k,
            "result_count": len(hits),
            "injected_into_prompt": injected,
            "context_chars": len(prompt_block),
            "source_kinds": source_kinds,
            "embedding_provider": self.embedding_provider_name,
            "embedding_model_id": self.embedding_model_id,
            "embedding_fingerprint": self.embedding_fingerprint,
            "collection_name": self.embedding_collection_name,
            "vector_backend": self.vector_backend_name,
            "fallback_used": self.embedding_fallback_used,
            "fallback_reason": self.embedding_fallback_reason,
            "hits": [
                {
                    "chunk_id": item.chunk_id,
                    "score": round(float(item.score), 4),
                    "source_kind": item.source_kind,
                    "source_ref_id": item.source_ref_id,
                    "title": item.title,
                    "importance": round(float(item.importance or 0.0), 3),
                    "embedding_fingerprint": item.embedding_fingerprint,
                }
                for item in hits
            ],
        }

    def _build_candidate_content(
        self,
        *,
        user_request: str,
        result_summary: str,
        response_text: str,
        recommendation: str,
        skill_name: str,
        task_category: str,
        changed_files: list[str],
        commands_run: list[str],
    ) -> str:
        parts = [
            f"用户目标：{user_request.strip()}",
            f"结果摘要：{(result_summary or response_text).strip()}",
        ]
        if recommendation.strip():
            parts.append(f"建议：{recommendation.strip()}")
        if changed_files:
            parts.append(f"变更文件：{', '.join(changed_files[:6])}")
        if commands_run:
            parts.append(f"执行命令：{'; '.join(commands_run[:4])}")
        if skill_name:
            parts.append(f"技能：{skill_name}")
        if task_category:
            parts.append(f"任务类别：{task_category}")
        return "\n".join(part for part in parts if part).strip()[:1600]

    def _build_decision_title(self, user_request: str, result_summary: str, skill_name: str) -> str:
        preferred = result_summary.strip() or user_request.strip() or skill_name.strip() or "decision"
        return preferred.splitlines()[0].strip()[:72]

    def _build_decision_card(
        self,
        *,
        user_request: str,
        result_summary: str,
        response_text: str,
        recommendation: str,
        changed_files: list[str],
        commands_run: list[str],
    ) -> str:
        parts = [
            f"决策主题：{self._truncate_title(user_request, limit=120)}",
            f"结论：{(result_summary or response_text).strip()[:480]}",
        ]
        if recommendation.strip():
            parts.append(f"建议：{recommendation.strip()[:220]}")
        if changed_files:
            parts.append(f"关联变更：{', '.join(changed_files[:8])}")
        if commands_run:
            parts.append(f"验证或命令：{'; '.join(commands_run[:4])}")
        return "\n".join(parts)

    def _maybe_persist_session_rollup(
        self,
        *,
        session_id: str,
        user_request: str,
        result_summary: str,
        recommendation: str,
        score_details: KnowledgeScoreDetails,
        settings: RAGSettings,
    ) -> dict[str, Any]:
        if not session_id:
            return {"created": False, "reason": "no_session", "item_id": "", "dedup_hit": False, "dedup_reason": ""}
        recent_messages = self.message_repo.list_recent(session_id, limit=max(settings.session_rollup_turn_threshold * 2, 20))
        existing_summary_count = self.memory_repo.count_by_type_for_session(session_id=session_id, memory_type="session_summary")
        decision = self.session_aggregator.should_rollup_session_summary(
            session_message_count=len(recent_messages),
            existing_summary_count=existing_summary_count,
            score=score_details.total_score,
            turn_threshold=settings.session_rollup_turn_threshold,
            threshold_summary=settings.importance_threshold_summary,
            max_summaries=settings.max_session_summaries_per_session,
            session_rollup_enabled=settings.session_rollup_enabled,
        )
        if not decision.should_rollup:
            logger.info("session rollup skipped reason=%s", decision.reason)
            return {"created": False, "reason": decision.reason, "item_id": "", "dedup_hit": False, "dedup_reason": ""}

        session_row = self.session_repo.get(session_id) or {}
        session_title = str(session_row.get("title", "") or user_request)
        content = self.session_aggregator.build_session_rollup(
            session_title=session_title,
            user_request=user_request,
            result_summary=result_summary,
            recommendation=recommendation,
            recent_messages=recent_messages,
        )
        item_id, persist_meta = self._persist_memory_card(
            memory_type="session_summary",
            source_kind="message_summary",
            title=self._truncate_title(session_title or user_request),
            content=content,
            session_id=session_id,
            tags=["session_summary"],
            importance=max(0.58, min(0.85, 0.5 + score_details.total_score / 10)),
            settings=settings,
            decision_status="confirmed",
            vectorize=True,
        )
        return {
            "created": True,
            "reason": decision.reason,
            "item_id": item_id,
            "dedup_hit": persist_meta["dedup_hit"],
            "dedup_reason": persist_meta["dedup_reason"],
        }

    def _persist_memory_card(
        self,
        *,
        memory_type: str,
        source_kind: str,
        title: str,
        content: str,
        session_id: str | None,
        tags: list[str],
        importance: float,
        settings: RAGSettings,
        source_message_ids: list[str] | None = None,
        decision_status: str = "confirmed",
        vectorize: bool = True,
    ) -> tuple[str, dict[str, Any]]:
        self._ensure_runtime(settings)
        canonical_key = self._build_canonical_key(memory_type=memory_type, title=title, content=content)
        if settings.dedup_enabled:
            existing = self.memory_repo.find_by_canonical_key(memory_type, canonical_key)
            if existing is not None and str(existing.get("decision_status", "confirmed")) != "rejected":
                existing_id = str(existing["id"])
                self.memory_repo.merge_into_existing(
                    item_id=existing_id,
                    content=content,
                    importance=importance,
                    source_session_id=session_id,
                    source_message_ids=source_message_ids,
                    merged_source_refs=[canonical_key],
                )
                if vectorize:
                    refreshed = self.memory_repo.get_item(existing_id) or existing
                    self._replace_memory_chunks(
                        source_kind=source_kind,
                        source_ref_id=existing_id,
                        title=str(refreshed.get("title", "") or title),
                        content=str(refreshed.get("content", "") or content),
                        session_id=session_id or "",
                        importance=float(refreshed.get("importance", importance) or importance),
                        tags=self._json_list(refreshed.get("tags_json")) or tags,
                        settings=settings,
                    )
                logger.info("knowledge_dedup_hit existing_item_id=%s reason=canonical_key", existing_id)
                return existing_id, {"dedup_hit": True, "dedup_reason": "canonical_key", "item_id": existing_id}

            near_hit = self._find_near_duplicate(content, source_kind=source_kind, settings=settings)
            if near_hit is not None:
                existing_id = near_hit["item_id"]
                self.memory_repo.merge_into_existing(
                    item_id=existing_id,
                    content=content,
                    importance=importance,
                    source_session_id=session_id,
                    source_message_ids=source_message_ids,
                    merged_source_refs=[canonical_key],
                    append_line=f"补充证据：{self._truncate_title(content, limit=180)}",
                )
                if vectorize:
                    refreshed = self.memory_repo.get_item(existing_id)
                    if refreshed is not None:
                        self._replace_memory_chunks(
                            source_kind=source_kind,
                            source_ref_id=existing_id,
                            title=str(refreshed.get("title", "") or title),
                            content=str(refreshed.get("content", "") or content),
                            session_id=session_id or "",
                            importance=float(refreshed.get("importance", importance) or importance),
                            tags=self._json_list(refreshed.get("tags_json")) or tags,
                            settings=settings,
                        )
                logger.info("knowledge_dedup_hit existing_item_id=%s reason=semantic score=%.3f", existing_id, near_hit["score"])
                return existing_id, {"dedup_hit": True, "dedup_reason": "semantic", "item_id": existing_id}

        item_id = self.memory_repo.save_item(
            memory_type=memory_type,
            title=title,
            content=content,
            importance=importance,
            decision_status=decision_status,
            source_session_id=session_id,
            source_message_ids=source_message_ids,
            tags=tags,
            evidence_count=1,
            canonical_key=canonical_key,
            merged_source_refs=[canonical_key],
        )
        if vectorize:
            self._replace_memory_chunks(
                source_kind=source_kind,
                source_ref_id=item_id,
                title=title,
                content=content,
                session_id=session_id or "",
                importance=importance,
                tags=tags,
                settings=settings,
            )
        return item_id, {"dedup_hit": False, "dedup_reason": "", "item_id": item_id}

    def _replace_memory_chunks(
        self,
        *,
        source_kind: str,
        source_ref_id: str,
        title: str,
        content: str,
        session_id: str,
        importance: float,
        tags: list[str],
        settings: RAGSettings,
    ) -> None:
        self._ensure_runtime(settings)
        assert self._vector_store is not None
        chunks = self.chunker.chunk_text(
            text=content,
            source_kind=source_kind,
            source_ref_id=source_ref_id,
            title=title,
            tags=tags,
            session_id=session_id,
            importance=importance,
        )
        rows = [
            {
                "id": chunk.id,
                "document_id": None,
                "source_kind": chunk.metadata.source_kind,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "token_estimate": chunk.token_estimate,
                "metadata": {
                    "source_kind": chunk.metadata.source_kind,
                    "source_ref_id": chunk.metadata.source_ref_id,
                    "title": chunk.metadata.title,
                    "tags": chunk.metadata.tags,
                    "created_at": chunk.metadata.created_at,
                    "session_id": chunk.metadata.session_id,
                    "importance": chunk.metadata.importance,
                    "document_id": chunk.metadata.document_id,
                    "embedding_provider": self.embedding_provider_name,
                    "embedding_model_id": self.embedding_model_id,
                    "embedding_dim": self._embedding_runtime.dimension if self._embedding_runtime else 0,
                    "embedding_fingerprint": self.embedding_fingerprint,
                },
                "embedding_fingerprint": self.embedding_fingerprint,
            }
            for chunk in chunks
        ]
        self.document_repo.replace_chunks(source_ref_id=source_ref_id, chunks=rows)
        self._vector_store.delete_by_source_ref_id(source_ref_id)
        try:
            self._vector_store.upsert_chunks(chunks)
        except Exception as exc:  # noqa: BLE001
            if self.embedding_provider_name != "hash":
                self._fallback_runtime(settings, f"upsert_failed:{exc}")
                assert self._vector_store is not None
                self._vector_store.delete_by_source_ref_id(source_ref_id)
                self._vector_store.upsert_chunks(chunks)
            else:
                raise

    def _find_near_duplicate(
        self,
        content: str,
        *,
        source_kind: str,
        settings: RAGSettings,
        exclude_source_ref_id: str = "",
    ) -> dict[str, Any] | None:
        if not content.strip() or self._retriever is None:
            return None
        try:
            hits = self._retriever.search(content, top_k=3, filters={"source_kind": source_kind})
        except Exception as exc:  # noqa: BLE001
            if self.embedding_provider_name != "hash":
                self._fallback_runtime(settings, f"dedup_search_failed:{exc}")
                assert self._retriever is not None
                hits = self._retriever.search(content, top_k=3, filters={"source_kind": source_kind})
            else:
                return None
        for hit in hits:
            if exclude_source_ref_id and hit.source_ref_id == exclude_source_ref_id:
                continue
            if hit.score >= settings.dedup_similarity_threshold and hit.source_ref_id:
                return {"item_id": hit.source_ref_id, "score": hit.score}
        return None

    def _build_canonical_key(self, *, memory_type: str, title: str, content: str) -> str:
        if memory_type == "decision_card":
            return fingerprint_text(f"{memory_type}\n{title}")
        preview = content[:280] if content else title
        return fingerprint_text(f"{memory_type}\n{title}\n{preview}")

    def _truncate_title(self, text: str, *, limit: int = 80) -> str:
        cleaned = " ".join(text.replace("\n", " ").split()).strip()
        return cleaned[:limit] if len(cleaned) > limit else cleaned

    def _json_list(self, value: Any) -> list[str]:
        if value in (None, "", []):
            return []
        try:
            data = json.loads(str(value))
        except Exception:
            return []
        return [str(item) for item in data if str(item).strip()]

    def _looks_like_text_document(self, file_path: str | Path) -> bool:
        return Path(file_path).suffix.lower() in {".txt", ".md", ".json", ".csv", ".py", ".yaml", ".yml"}

    def _fallback_runtime(self, settings: RAGSettings, reason: str) -> None:
        logger.warning("Embedding runtime fallback triggered reason=%s", reason)
        self._embedding_runtime = self._build_hash_runtime(settings, reason)
        self._vector_store = build_vector_store(settings=settings, embedding_runtime=self._embedding_runtime, db=self.db)
        self._retriever = RAGRetriever(self._vector_store)
        self._ingestion = IngestionPipeline(
            memory_repo=self.memory_repo,
            document_repo=self.document_repo,
            vector_store=self._vector_store,
            chunker=self.chunker,
        )
        self._runtime_settings_signature = repr(settings.to_dict())

    def _get_runtime_registry(self) -> dict[str, Any]:
        registry = self.settings_repo.get_json(self.RUNTIME_REGISTRY_KEY, {}) or {}
        return registry if isinstance(registry, dict) else {}

    def _save_runtime_registry(self, registry: dict[str, Any]) -> None:
        self.settings_repo.set_json(self.RUNTIME_REGISTRY_KEY, registry)

    def _register_embedding_runtime(self, runtime: EmbeddingRuntime, settings: RAGSettings) -> None:
        registry = self._get_runtime_registry()
        registry[runtime.fingerprint] = {
            "provider_name": runtime.provider_name,
            "model_id": runtime.model_id,
            "dimension": runtime.dimension,
            "fingerprint": runtime.fingerprint,
            "collection_name": runtime.collection_name,
            "settings": settings.to_dict(),
        }
        self._save_runtime_registry(registry)

    def _resolve_registered_runtime(self, fingerprint: str) -> tuple[EmbeddingRuntime | None, RAGSettings | None]:
        descriptor = self._get_runtime_registry().get(fingerprint)
        if not isinstance(descriptor, dict):
            return None, None
        settings_payload = descriptor.get("settings")
        if not isinstance(settings_payload, dict):
            return None, None
        runtime_settings = RAGSettings.from_dict(settings_payload)
        runtime = self.embedding_factory.create(runtime_settings)
        hydrated = self._hydrate_runtime(runtime, runtime_settings)
        return hydrated, runtime_settings

    def _ensure_runtime(self, settings: RAGSettings) -> None:
        signature = repr(settings.to_dict())
        if signature == self._runtime_settings_signature and self._vector_store is not None and self._retriever is not None and self._ingestion is not None:
            return

        previous_provider = self.embedding_provider_name
        previous_fingerprint = self.embedding_fingerprint

        configured_runtime = self.embedding_factory.create(settings)
        try:
            configured_runtime = self._hydrate_runtime(configured_runtime, settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding_provider_fallback provider=%s reason=%s", configured_runtime.provider_name, exc)
            configured_runtime = self._build_hash_runtime(settings, f"hydrate_failed:{exc}")

        self._register_embedding_runtime(configured_runtime, settings)
        active_fingerprint = settings.active_embedding_fingerprint.strip() or configured_runtime.fingerprint

        active_runtime = configured_runtime
        active_settings = settings
        if active_fingerprint and active_fingerprint != configured_runtime.fingerprint:
            try:
                resolved_runtime, resolved_settings = self._resolve_registered_runtime(active_fingerprint)
                if resolved_runtime is not None and resolved_settings is not None:
                    active_runtime = resolved_runtime
                    active_settings = resolved_settings
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to resolve active embedding fingerprint=%s reason=%s", active_fingerprint, exc)

        self._embedding_runtime = active_runtime
        self._vector_store = build_vector_store(settings=active_settings, embedding_runtime=active_runtime, db=self.db)
        self._retriever = RAGRetriever(self._vector_store)
        self._ingestion = IngestionPipeline(
            memory_repo=self.memory_repo,
            document_repo=self.document_repo,
            vector_store=self._vector_store,
            chunker=self.chunker,
        )
        self._runtime_settings_signature = signature

        if previous_provider and (previous_provider != active_runtime.provider_name or previous_fingerprint != active_runtime.fingerprint):
            logger.info(
                "embedding_provider_switched previous=%s/%s current=%s/%s",
                previous_provider,
                previous_fingerprint,
                active_runtime.provider_name,
                active_runtime.fingerprint,
            )

        if active_runtime.fallback_used:
            logger.warning(
                "Embedding provider fallback engaged provider=%s reason=%s",
                active_runtime.provider_name,
                active_runtime.fallback_reason,
            )
        else:
            logger.info(
                "Embedding provider initialized provider=%s model=%s fingerprint=%s backend=%s",
                active_runtime.provider_name,
                active_runtime.model_id,
                active_runtime.fingerprint,
                self.vector_backend_name,
            )

    def _build_context_block(self, chunks: list[Any], *, max_chars: int) -> dict[str, Any]:
        if not chunks:
            return {
                "prompt_block": "",
                "item_count": 0,
                "truncated": False,
                "included_chunk_ids": [],
                "max_chars": max_chars,
            }
        lines = ["[Retrieved Context]"]
        used = len(lines[0]) + 1
        included_chunk_ids: list[str] = []
        truncated = False
        for index, chunk in enumerate(chunks, start=1):
            header = f"{index}. 来源: {chunk.source_kind} / {chunk.title or chunk.source_ref_id} / {chunk.created_at or 'unknown'}"
            content = chunk.content.strip()
            remaining = max_chars - used - len(header) - 16
            if remaining <= 0:
                truncated = True
                break
            snippet = content[:remaining].strip()
            block = f"{header}\n   内容: {snippet}"
            lines.append(block)
            used += len(block) + 1
            included_chunk_ids.append(str(chunk.chunk_id))
            if len(snippet) < len(content):
                truncated = True
            if used >= max_chars:
                truncated = True
                break
        prompt_block = "\n".join(lines) if len(lines) > 1 else ""
        return {
            "prompt_block": prompt_block,
            "item_count": len(included_chunk_ids),
            "truncated": truncated or len(included_chunk_ids) < len(chunks),
            "included_chunk_ids": included_chunk_ids,
            "max_chars": max_chars,
        }

    def _build_debug_snapshot(
        self,
        *,
        query: str,
        hits: list[Any],
        top_k: int,
        prompt_block: str,
        context_meta: dict[str, Any],
        source_kinds: list[str],
        injected: bool,
        max_context_chars: int,
    ) -> dict[str, Any]:
        included_chunk_ids = {str(item) for item in context_meta.get("included_chunk_ids", [])}
        return {
            "rag_enabled": True,
            "triggered": True,
            "query": query,
            "top_k": top_k,
            "result_count": len(hits),
            "injected_into_prompt": injected,
            "context_chars": len(prompt_block),
            "final_injected_context_preview": prompt_block,
            "injected_context_item_count": int(context_meta.get("item_count", 0) or 0),
            "injected_context_truncated": bool(context_meta.get("truncated", False)),
            "injected_context_chars": len(prompt_block),
            "max_context_chars": max_context_chars,
            "final_order": list(context_meta.get("included_chunk_ids", [])),
            "source_kinds": source_kinds,
            "embedding_provider": self.embedding_provider_name,
            "embedding_model_id": self.embedding_model_id,
            "embedding_fingerprint": self.embedding_fingerprint,
            "collection_name": self.embedding_collection_name,
            "vector_backend": self.vector_backend_name,
            "fallback_used": self.embedding_fallback_used,
            "fallback_reason": self.embedding_fallback_reason,
            "hits": [
                {
                    "chunk_id": item.chunk_id,
                    "score": round(float(item.score), 4),
                    "source_kind": item.source_kind,
                    "source_ref_id": item.source_ref_id,
                    "title": item.title,
                    "importance": round(float(item.importance or 0.0), 3),
                    "embedding_fingerprint": item.embedding_fingerprint,
                    "injected_into_prompt": str(item.chunk_id) in included_chunk_ids,
                }
                for item in hits
            ],
        }


_RAG_MANAGER: RagManager | None = None


def get_rag_manager() -> RagManager:
    global _RAG_MANAGER
    if _RAG_MANAGER is None:
        _RAG_MANAGER = RagManager()
    return _RAG_MANAGER


def load_rag_settings() -> RAGSettings:
    return get_rag_manager().load_settings()


def save_rag_settings(settings: RAGSettings) -> None:
    get_rag_manager().save_settings(settings)
