from __future__ import annotations

import unittest
from pathlib import Path

from app.rag import RAGSettings
from app.rag.rag_manager import RagManager
from app.storage.db import AppDatabase
from app.storage.repositories.memory_repo import MemoryRepo
from app.storage.repositories.message_repo import MessageRepo
from app.storage.repositories.session_repo import SessionRepo


class KnowledgeQualityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = Path("data/test_knowledge_quality")
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.db = AppDatabase(self.test_dir / "fairy_test.db")
        self.memory_repo = MemoryRepo(self.db)
        self.message_repo = MessageRepo(self.db)
        self.session_repo = SessionRepo(self.db)
        self.rag = RagManager(self.db)
        self.rag.save_settings(
            RAGSettings(
                embedding_enabled=True,
                embedding_provider="hash",
                session_rollup_enabled=True,
                session_rollup_turn_threshold=6,
                max_session_summaries_per_session=2,
            )
        )

    def tearDown(self) -> None:
        for file_path in self.test_dir.glob("*"):
            if file_path.is_file():
                file_path.unlink(missing_ok=True)

    def test_low_value_chatter_is_not_persisted(self) -> None:
        session_id = self.session_repo.create_session(
            title="Noise Session",
            mode="normal_mode",
            provider="local",
            model="test",
        )
        stats = self.rag.maybe_store_task_knowledge(
            user_request="我现在有点乱，先不管了。",
            result_summary="哈哈先算了，没有形成稳定方案。",
            response_text="哈哈先算了。",
            recommendation="",
            task_category="casual_chat",
            skill_name="chat",
            session_id=session_id,
        )
        self.assertEqual(stats["session_summary"], 0)
        self.assertEqual(stats["decision_card"], 0)
        self.assertEqual(stats["pending_decision"], 0)
        self.assertEqual(self.memory_repo.list_recent(limit=10), [])

    def test_decision_candidate_is_pending_and_deduplicated(self) -> None:
        session_id = self.session_repo.create_session(
            title="Decision Session",
            mode="normal_mode",
            provider="local",
            model="test",
        )
        payload = dict(
            user_request="把 persona 改成设置开关",
            result_summary="结论：persona 子系统改成设置开关，默认关闭。",
            response_text="结论：persona 子系统改成设置开关，默认关闭。",
            recommendation="默认先关闭，必要时再开启。",
            task_category="coding_help",
            skill_name="terminal-agent",
            session_id=session_id,
        )
        self.rag.maybe_store_task_knowledge(**payload)
        self.rag.maybe_store_task_knowledge(**payload)
        items = self.memory_repo.list_recent(memory_type="decision_card", limit=10)
        self.assertEqual(len(items), 1)
        self.assertEqual(str(items[0].get("decision_status", "")), "pending")
        self.assertGreaterEqual(int(items[0].get("evidence_count", 0) or 0), 2)

    def test_pending_decision_can_be_confirmed(self) -> None:
        session_id = self.session_repo.create_session(
            title="Confirm Decision Session",
            mode="normal_mode",
            provider="local",
            model="test",
        )
        self.rag.maybe_store_task_knowledge(
            user_request="把 persona 改成设置开关",
            result_summary="结论：persona 子系统改成设置开关，默认关闭。",
            response_text="结论：persona 子系统改成设置开关，默认关闭。",
            recommendation="默认先关闭，必要时再开启。",
            task_category="coding_help",
            skill_name="terminal-agent",
            session_id=session_id,
        )
        pending = self.rag.list_pending_decisions(limit=10)
        self.assertEqual(len(pending), 1)
        result = self.rag.confirm_decision_candidate(str(pending[0]["id"]))
        self.assertTrue(result["ok"])
        item = self.memory_repo.get_item(str(pending[0]["id"]))
        self.assertEqual(str(item.get("decision_status", "")), "confirmed")

    def test_session_rollup_uses_turn_threshold(self) -> None:
        session_id = self.session_repo.create_session(
            title="Rollup Session",
            mode="normal_mode",
            provider="local",
            model="test",
        )
        for index in range(6):
            self.message_repo.add_message(
                session_id=session_id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"消息 {index}：当前规则和模块职责已经逐渐明确。",
            )
        stats = self.rag.maybe_store_task_knowledge(
            user_request="整理当前规则",
            result_summary="当前架构规则已经明确，模块职责和默认配置都已收敛。",
            response_text="当前架构规则已经明确，模块职责和默认配置都已收敛。",
            recommendation="继续按当前规则推进。",
            task_category="planning",
            skill_name="terminal-agent",
            session_id=session_id,
        )
        self.assertEqual(stats["session_summary"], 1)
        items = self.memory_repo.list_recent(memory_type="session_summary", limit=10)
        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
