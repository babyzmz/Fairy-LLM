from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from app.memory.age_decay import age_decay_weight
from app.memory.memory_schema import SemanticMemoryRecord


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[一-鿿]", flags=re.UNICODE)
_STOP_TOKENS = frozenset(
    {
        "的",
        "了",
        "和",
        "是",
        "在",
        "有",
        "我",
        "你",
        "他",
        "她",
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "to",
        "in",
        "on",
        "for",
    }
)


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    return [token.lower() for token in _TOKEN_RE.findall(text) if token.lower() not in _STOP_TOKENS]


def keyword_overlap_score(query: str | list[str], target_text: str) -> float:
    query_tokens = tokenize(query) if isinstance(query, str) else list(query)
    if not query_tokens:
        return 0.0
    target_tokens = tokenize(target_text)
    if not target_tokens:
        return 0.0
    target_counts = Counter(target_tokens)
    query_counts = Counter(query_tokens)
    overlap = sum(min(query_counts[token], target_counts[token]) for token in query_counts)
    return overlap / math.sqrt(len(query_tokens) * len(target_tokens))


@dataclass(slots=True)
class ScoredMemory:
    record: SemanticMemoryRecord | dict
    score: float
    breakdown: dict[str, float]


def score_semantic_records(
    query: str,
    records: Iterable[SemanticMemoryRecord],
    *,
    vector_weight: float = 0.6,
    keyword_weight: float = 0.25,
    freshness_weight: float = 0.15,
    half_life_days: float = 14.0,
) -> list[ScoredMemory]:
    scored: list[ScoredMemory] = []
    for record in records:
        vector_score = float(getattr(record, "score", 0.0) or 0.0)
        keyword_score = keyword_overlap_score(query, record.content)
        updated_at = (record.metadata or {}).get("updated_at") if isinstance(getattr(record, "metadata", None), dict) else None
        freshness = age_decay_weight(updated_at, half_life_days=half_life_days)
        combined = (
            vector_weight * vector_score
            + keyword_weight * keyword_score
            + freshness_weight * freshness
        )
        scored.append(
            ScoredMemory(
                record=record,
                score=combined,
                breakdown={
                    "vector": vector_score,
                    "keyword": keyword_score,
                    "freshness": freshness,
                },
            )
        )
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored


def find_relevant_memories(
    query: str,
    records: Iterable[SemanticMemoryRecord],
    *,
    top_k: int = 5,
    min_score: float = 0.05,
    **kwargs,
) -> list[SemanticMemoryRecord]:
    scored = score_semantic_records(query, records, **kwargs)
    return [item.record for item in scored if item.score >= min_score][:top_k]
