from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.memory.age_decay import age_decay_weight, freshness_score
from app.memory.memory_schema import SemanticMemoryRecord
from app.memory.relevance_scorer import find_relevant_memories, keyword_overlap_score, score_semantic_records, tokenize


class TokenizerTests(unittest.TestCase):
    def test_strips_stop_tokens(self) -> None:
        tokens = tokenize("我有的攻略 build")
        self.assertNotIn("的", tokens)
        self.assertIn("攻", tokens)
        self.assertIn("略", tokens)
        self.assertIn("build", tokens)

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(tokenize(""), [])

    def test_keyword_overlap_positive(self) -> None:
        score = keyword_overlap_score("BOSS 攻略", "BOSS 攻略：先磨血再上技能")
        self.assertGreater(score, 0)


class AgeDecayTests(unittest.TestCase):
    def test_fresh_memory_near_one(self) -> None:
        now = datetime.now(timezone.utc)
        weight = age_decay_weight(now.isoformat(), now=now)
        self.assertAlmostEqual(weight, 1.0, places=2)

    def test_old_memory_decays(self) -> None:
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=28)).isoformat()
        weight = age_decay_weight(old, now=now, half_life_days=14.0)
        self.assertLess(weight, 0.3)
        self.assertGreater(weight, 0.2)

    def test_missing_returns_neutral(self) -> None:
        self.assertEqual(age_decay_weight(None), 0.5)


class RelevanceScorerTests(unittest.TestCase):
    def _record(self, content: str, *, score: float = 0.5, updated_at: str | None = None) -> SemanticMemoryRecord:
        return SemanticMemoryRecord(
            id=content[:6],
            scope="game",
            content=content,
            score=score,
            importance=0.5,
            confidence=0.7,
            tags=[],
            metadata={"updated_at": updated_at} if updated_at else {},
        )

    def test_relevant_record_ranked_first(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        records = [
            self._record("游戏闲谈和聊天", score=0.2, updated_at=now),
            self._record("BOSS 阶段二要先躲技能再上 build", score=0.8, updated_at=now),
        ]
        ranked = score_semantic_records("BOSS 攻略 build", records)
        self.assertGreater(ranked[0].score, ranked[1].score)
        self.assertIn("BOSS", ranked[0].record.content)

    def test_find_relevant_filters_by_min_score(self) -> None:
        irrelevant = self._record("天气真好", score=0.05)
        result = find_relevant_memories("BOSS 攻略", [irrelevant], min_score=0.5)
        self.assertEqual(result, [])

    def test_freshness_score_alias(self) -> None:
        now = datetime.now(timezone.utc)
        self.assertAlmostEqual(freshness_score(now.isoformat()), 1.0, places=2)


if __name__ == "__main__":
    unittest.main()
