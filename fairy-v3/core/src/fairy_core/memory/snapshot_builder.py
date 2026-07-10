from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from fairy_core.domain.models import ScopeContract
from fairy_core.memory.policy import MemoryPolicy
from fairy_core.memory.ports import MemoryRepository
from fairy_core.memory.retrieval_models import (
    MemoryProjectionHealth,
    MemorySearchHit,
    MemorySnapshot,
    MemorySnapshotItem,
    MemorySnapshotStatus,
    ProjectionState,
)
from fairy_core.memory.retrieval_ports import MemorySearchIndex
from fairy_core.memory.snapshot_candidates import SnapshotCandidateCollector
from fairy_core.memory.snapshot_selection import (
    DEFAULT_SNAPSHOT_TOKEN_BUDGET,
    HARD_SNAPSHOT_TOKEN_CEILING,
    SnapshotBudgetSelector,
    Utf8ByteTokenCounter,
)

_SEARCH_LIMIT = 100
_MAX_QUERY_LENGTH = 10_000


def _now() -> datetime:
    return datetime.now(UTC)


class DeterministicMemorySnapshotBuilder:
    """Builds one bounded immutable memory manifest for a Task."""

    def __init__(
        self,
        *,
        memory_repository: MemoryRepository,
        search_index: MemorySearchIndex,
        policy: MemoryPolicy | None = None,
        token_counter: Utf8ByteTokenCounter | None = None,
        clock: Callable[[], datetime] = _now,
        policy_version: str = "hermes-lexical-v1",
        projection_generation: int = 1,
    ) -> None:
        if not policy_version.strip():
            raise ValueError("policy_version is required")
        if projection_generation < 1:
            raise ValueError("projection_generation must be positive")
        effective_policy = policy or MemoryPolicy()
        effective_tokens = token_counter or Utf8ByteTokenCounter()
        self._search = search_index
        self._clock = clock
        self._policy_version = policy_version.strip()
        self._generation = projection_generation
        self._collector = SnapshotCandidateCollector(
            memory_repository=memory_repository,
            policy=effective_policy,
            token_counter=effective_tokens,
            projection_generation=projection_generation,
        )
        self._selector = SnapshotBudgetSelector()

    def build(
        self,
        *,
        scope: ScopeContract,
        query: str,
        source_watermark_cursor: int,
    ) -> MemorySnapshot:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Snapshot query is required")
        if len(normalized_query) > _MAX_QUERY_LENGTH:
            raise ValueError("Snapshot query cannot exceed 10,000 characters")
        if source_watermark_cursor < 0:
            raise ValueError("source_watermark_cursor cannot be negative")

        at = self._clock()
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Snapshot clock must return a timezone-aware datetime")

        health, degraded_reason = self._projection_health(source_watermark_cursor, at)
        hits: tuple[MemorySearchHit, ...] = ()
        if degraded_reason is None:
            try:
                hits = self._search.search(
                    scope=scope,
                    query=normalized_query,
                    generation=self._generation,
                    limit=_SEARCH_LIMIT,
                )
            except Exception:
                health = MemoryProjectionHealth(
                    generation=self._generation,
                    state=ProjectionState.FAILED,
                    source_watermark_cursor=source_watermark_cursor,
                    projected_watermark_cursor=health.projected_watermark_cursor,
                    last_error_code="MEMORY_SEARCH_FAILED",
                    updated_at=at,
                )
                degraded_reason = "MEMORY_SEARCH_FAILED"

        valid_hits = self._collector.normalize_hits(
            hits,
            scope=scope,
            source_watermark_cursor=source_watermark_cursor,
        )
        claim_candidates, provenance_observation_ids = self._collector.claim_candidates(
            scope=scope,
            query=normalized_query,
            source_watermark_cursor=source_watermark_cursor,
            hits=valid_hits,
            at=at,
        )
        if degraded_reason is None:
            history_candidates = self._collector.search_history_candidates(
                scope=scope,
                query=normalized_query,
                source_watermark_cursor=source_watermark_cursor,
                hits=valid_hits,
                excluded_observation_ids=provenance_observation_ids,
            )
        else:
            history_candidates = self._collector.fallback_history_candidates(
                scope=scope,
                query=normalized_query,
                source_watermark_cursor=source_watermark_cursor,
                excluded_observation_ids=provenance_observation_ids,
            )

        ordered = self._selector.select((*claim_candidates, *history_candidates))
        items = tuple(
            MemorySnapshotItem.create(
                ordinal=ordinal,
                source_kind=candidate.source_kind,
                source_id=candidate.source_id,
                source_revision=candidate.source_revision,
                namespace=candidate.namespace,
                selection_reason=candidate.selection_reason,
                authority=candidate.authority,
                score_components=candidate.score_components,
                rendered_text=candidate.rendered_text,
                token_count=candidate.token_count,
            )
            for ordinal, candidate in enumerate(ordered)
        )
        status = (
            MemorySnapshotStatus.READY if degraded_reason is None else MemorySnapshotStatus.DEGRADED
        )
        return MemorySnapshot.create(
            project_id=scope.project_id,
            conversation_id=scope.conversation_id,
            task_id=scope.task_id,
            base_version_id=scope.base_version_id,
            target_version_id=scope.target_version_id,
            policy_version=self._policy_version,
            source_watermark_cursor=source_watermark_cursor,
            projection_generation=self._generation,
            projection_watermark_cursor=health.projected_watermark_cursor,
            projection_state=health.state,
            status=status,
            degraded_reason=degraded_reason,
            items=items,
        )

    def _projection_health(
        self,
        source_watermark_cursor: int,
        at: datetime,
    ) -> tuple[MemoryProjectionHealth, str | None]:
        try:
            health = self._search.health(
                generation=self._generation,
                source_watermark_cursor=source_watermark_cursor,
            )
        except Exception:
            failed = MemoryProjectionHealth(
                generation=self._generation,
                state=ProjectionState.FAILED,
                source_watermark_cursor=source_watermark_cursor,
                projected_watermark_cursor=0,
                last_error_code="MEMORY_PROJECTION_HEALTH_FAILED",
                updated_at=at,
            )
            return failed, "MEMORY_PROJECTION_HEALTH_FAILED"

        if health.generation != self._generation:
            failed = MemoryProjectionHealth(
                generation=self._generation,
                state=ProjectionState.FAILED,
                source_watermark_cursor=source_watermark_cursor,
                projected_watermark_cursor=0,
                last_error_code="MEMORY_PROJECTION_GENERATION_MISMATCH",
                updated_at=at,
            )
            return failed, "MEMORY_PROJECTION_GENERATION_MISMATCH"
        if (
            health.state is ProjectionState.READY
            and health.projected_watermark_cursor < source_watermark_cursor
        ):
            stale = MemoryProjectionHealth(
                generation=self._generation,
                state=ProjectionState.STALE,
                source_watermark_cursor=source_watermark_cursor,
                projected_watermark_cursor=health.projected_watermark_cursor,
                last_error_code="MEMORY_PROJECTION_STALE",
                updated_at=at,
            )
            return stale, "MEMORY_PROJECTION_STALE"
        if health.state is not ProjectionState.READY:
            reason = health.last_error_code or (f"MEMORY_PROJECTION_{health.state.value.upper()}")
            return health, reason
        return health, None


__all__ = [
    "DEFAULT_SNAPSHOT_TOKEN_BUDGET",
    "HARD_SNAPSHOT_TOKEN_CEILING",
    "DeterministicMemorySnapshotBuilder",
    "Utf8ByteTokenCounter",
]
