from __future__ import annotations

from collections.abc import Iterable, Mapping
from uuid import UUID

from fairy_core.domain.errors import MemorySnapshotTooLargeError
from fairy_core.memory.models import MemoryNamespace
from fairy_core.memory.policy import MEMORY_AUTHORITY_PRECEDENCE
from fairy_core.memory.retrieval_models import MemorySourceKind
from fairy_core.memory.snapshot_candidates import SnapshotCandidate

DEFAULT_SNAPSHOT_TOKEN_BUDGET = 2_400
HARD_SNAPSHOT_TOKEN_CEILING = 3_000
USER_PROFILE_TOKEN_BUDGET = 400
PROJECT_CANONICAL_TOKEN_BUDGET = 1_000
CONVERSATION_DRAFT_TOKEN_BUDGET = 500
HISTORY_TOKEN_BUDGET = 500


class Utf8ByteTokenCounter:
    """A stable conservative ceiling that is independent of model tokenizers."""

    @staticmethod
    def count(text: str) -> int:
        return len(text.encode("utf-8", errors="strict"))


class SnapshotBudgetSelector:
    """Applies deterministic authority ordering and section token budgets."""

    def select(self, candidates: Iterable[SnapshotCandidate]) -> tuple[SnapshotCandidate, ...]:
        deduplicated: dict[tuple[MemorySourceKind, UUID, int | None], SnapshotCandidate] = {}
        for candidate in sorted(candidates, key=rank_candidate):
            deduplicated.setdefault(candidate.key, candidate)
        values = list(deduplicated.values())
        required_keys = {
            candidate.key
            for candidate in values
            if candidate.namespace is MemoryNamespace.PROJECT_CANONICAL
            and candidate.source_kind is MemorySourceKind.CLAIM_REVISION
            and (candidate.exact_match or candidate.conflict_set_id is not None)
        }
        required = sorted(
            (candidate for candidate in values if candidate.key in required_keys),
            key=rank_candidate,
        )
        required_tokens = sum(candidate.token_count for candidate in required)
        if required_tokens > HARD_SNAPSHOT_TOKEN_CEILING:
            raise MemorySnapshotTooLargeError(
                "Required exact or conflicted Project Canonical Memory exceeds 3,000 tokens"
            )

        target_budget = max(DEFAULT_SNAPSHOT_TOKEN_BUDGET, required_tokens)
        selected = {candidate.key: candidate for candidate in required}
        remaining = target_budget - required_tokens

        profile_candidates = self._unselected_section(values, selected, "profile")
        profile_allocation = min(USER_PROFILE_TOKEN_BUDGET, remaining)
        chosen, used = self._fit(profile_candidates, profile_allocation)
        selected.update((candidate.key, candidate) for candidate in chosen)
        remaining -= used
        profile_carry = profile_allocation - used

        canonical_candidates = self._unselected_section(values, selected, "canonical")
        canonical_base = max(0, PROJECT_CANONICAL_TOKEN_BUDGET - required_tokens)
        canonical_allocation = min(remaining, canonical_base + profile_carry)
        chosen, used = self._fit(canonical_candidates, canonical_allocation)
        selected.update((candidate.key, candidate) for candidate in chosen)
        remaining -= used
        canonical_carry = canonical_allocation - used

        draft_candidates = self._unselected_section(values, selected, "draft")
        draft_allocation = min(
            remaining,
            CONVERSATION_DRAFT_TOKEN_BUDGET + canonical_carry,
        )
        chosen, used = self._fit(draft_candidates, draft_allocation)
        selected.update((candidate.key, candidate) for candidate in chosen)
        remaining -= used

        history_candidates = self._unselected_section(values, selected, "history")
        history_allocation = min(
            remaining,
            HISTORY_TOKEN_BUDGET + (draft_allocation - used),
        )
        chosen, _used = self._fit(history_candidates, history_allocation)
        selected.update((candidate.key, candidate) for candidate in chosen)
        return tuple(sorted(selected.values(), key=rank_candidate))

    @staticmethod
    def _fit(
        candidates: list[SnapshotCandidate],
        budget: int,
    ) -> tuple[list[SnapshotCandidate], int]:
        selected: list[SnapshotCandidate] = []
        used = 0
        for candidate in sorted(candidates, key=rank_candidate):
            if used + candidate.token_count <= budget:
                selected.append(candidate)
                used += candidate.token_count
        return selected, used

    @staticmethod
    def _unselected_section(
        candidates: list[SnapshotCandidate],
        selected: Mapping[
            tuple[MemorySourceKind, UUID, int | None],
            SnapshotCandidate,
        ],
        section: str,
    ) -> list[SnapshotCandidate]:
        return [
            candidate
            for candidate in candidates
            if candidate.section == section and candidate.key not in selected
        ]


def rank_candidate(candidate: SnapshotCandidate) -> tuple[object, ...]:
    if (
        candidate.namespace is MemoryNamespace.PROJECT_CANONICAL
        and candidate.source_kind is MemorySourceKind.CLAIM_REVISION
    ):
        category = 0 if candidate.exact_match else 1
    elif (
        candidate.namespace is MemoryNamespace.USER_PROFILE
        and candidate.source_kind is MemorySourceKind.CLAIM_REVISION
    ):
        category = 2 if candidate.exact_match else 3
    elif (
        candidate.namespace is MemoryNamespace.CONVERSATION_DRAFT
        and candidate.source_kind is MemorySourceKind.CLAIM_REVISION
    ):
        category = 4
    else:
        category = 5
    return (
        category,
        -MEMORY_AUTHORITY_PRECEDENCE[candidate.authority],
        -int(candidate.exact_match),
        -candidate.lexical_score,
        -candidate.source_cursor,
        -candidate.confidence,
        candidate.source_kind.value,
        str(candidate.source_id),
        candidate.source_revision or 0,
    )


__all__ = [
    "DEFAULT_SNAPSHOT_TOKEN_BUDGET",
    "HARD_SNAPSHOT_TOKEN_CEILING",
    "SnapshotBudgetSelector",
    "Utf8ByteTokenCounter",
]
