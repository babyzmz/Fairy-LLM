from __future__ import annotations

import unittest
from pathlib import Path

from app.rag.chunker import TextChunker
from app.rag.embedding_service import EmbeddingRuntime, HashEmbeddingService
from app.rag.ingestion_pipeline import IngestionPipeline
from app.rag.rag_manager import RagManager
from app.rag.rag_schema import RAGSettings
from app.rag.retriever import RAGRetriever
from app.rag.vector_store import SQLiteVectorStore
from app.storage.db import AppDatabase
from app.storage.repositories.document_repo import DocumentRepo
from app.storage.repositories.memory_repo import MemoryRepo
from app.storage.repositories.session_repo import SessionRepo
from app.storage.repositories.settings_repo import SettingsRepo


class RagPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = Path("data/test_rag_pipeline")
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.db = AppDatabase(self.test_dir / "fairy_test.db")
        self.settings_repo = SettingsRepo(self.db)
        self.memory_repo = MemoryRepo(self.db)
        self.document_repo = DocumentRepo(self.db)
        self.session_repo = SessionRepo(self.db)
        hash_service = HashEmbeddingService()
        runtime = EmbeddingRuntime(
            service=hash_service,
            provider_name="hash",
            model_id="hash-v1",
            dimension=hash_service.dimension_hint,
            fingerprint="hash-v1",
            collection_name="test_hash-v1",
        )
        self.vector_store = SQLiteVectorStore(self.db, hash_service, runtime)

    def tearDown(self) -> None:
        for file_path in self.test_dir.glob("*"):
            if file_path.is_file():
                file_path.unlink(missing_ok=True)

    def test_settings_round_trip(self) -> None:
        settings = RAGSettings(rag_enabled=True, rag_top_k=3, rag_max_context_chars=900)
        self.settings_repo.set_json("rag_settings", settings.to_dict())
        loaded = RAGSettings.from_dict(self.settings_repo.get_json("rag_settings"))
        self.assertEqual(loaded.rag_top_k, 3)
        self.assertEqual(loaded.rag_max_context_chars, 900)

    def test_ingest_and_retrieve_decision_card(self) -> None:
        ingestion = IngestionPipeline(
            memory_repo=self.memory_repo,
            document_repo=self.document_repo,
            vector_store=self.vector_store,
            chunker=TextChunker(),
        )
        session_id = self.session_repo.create_session(
            title="RAG Test Session",
            mode="normal_mode",
            provider="local_server",
            model="Qwen3.5-4B-Q4_K_M",
        )
        item_id = ingestion.save_decision_card(
            title="game mode",
            content="决策主题：game mode\n结论：game mode 默认走云端 provider，本地只作为 fallback。",
            session_id=session_id,
            tags=["game_mode"],
        )
        self.assertTrue(item_id.startswith("mem_"))
        hits = RAGRetriever(self.vector_store).search("之前 game mode 是怎么定的", top_k=3)
        self.assertTrue(hits)
        self.assertEqual(hits[0].source_kind, "decision_card")

    def test_retrieval_snapshot_contains_final_injected_context_preview(self) -> None:
        rag = RagManager(self.db)
        rag.save_settings(
            RAGSettings(
                rag_enabled=True,
                rag_top_k=3,
                rag_max_context_chars=400,
                embedding_enabled=True,
                embedding_provider="hash",
                vector_backend="sqlite",
            )
        )
        session_id = self.session_repo.create_session(
            title="Debug Snapshot Session",
            mode="normal_mode",
            provider="local_server",
            model="Qwen3.5-4B-Q4_K_M",
        )
        rag.save_decision_card(
            title="persona system switch",
            content="结论：persona 子系统改成设置开关，默认关闭，必要时再开启。",
            session_id=session_id,
            tags=["persona", "decision_card"],
        )
        result = rag.retrieve_context("之前 persona 是怎么定的？")
        snapshot = result.debug_snapshot
        self.assertTrue(snapshot.get("triggered"))
        self.assertTrue(snapshot.get("injected_into_prompt"))
        self.assertGreaterEqual(int(snapshot.get("injected_context_item_count", 0) or 0), 1)
        self.assertIn("[Retrieved Context]", str(snapshot.get("final_injected_context_preview", "")))
        self.assertIn("decision_card", snapshot.get("source_kinds", []))


if __name__ == "__main__":
    unittest.main()
