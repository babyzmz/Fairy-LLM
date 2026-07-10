from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from fairy_core.domain.errors import MemoryForgottenError
from fairy_core.domain.models import ScopeContract
from fairy_core.memory.models import (
    ClaimStatus,
    MemoryAuthority,
    MemoryClaim,
    MemoryClaimRevision,
    MemoryNamespace,
    MemoryObservation,
)
from fairy_core.memory.policy import MEMORY_AUTHORITY_PRECEDENCE, MemoryPolicy
from fairy_core.memory.ports import MemoryRepository
from fairy_core.memory.retrieval_models import (
    MemorySearchHit,
    MemorySelectionReason,
    MemorySourceKind,
)
from fairy_core.memory.snapshot_rendering import (
    is_exact_query_match,
    render_claim,
    render_observation,
)
from fairy_core.memory.snapshot_scope import (
    claim_is_in_scope,
    document_is_in_scope,
    observation_is_in_scope,
    readable_namespaces,
    repository_scope,
)

_FALLBACK_OBSERVATION_LIMIT = 100
_MAX_AUTHORITY_PRECEDENCE = max(MEMORY_AUTHORITY_PRECEDENCE.values())


class _TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


@dataclass(frozen=True, slots=True)
class SnapshotCandidate:
    source_kind: MemorySourceKind
    source_id: UUID
    source_revision: int | None
    namespace: MemoryNamespace | None
    authority: MemoryAuthority
    selection_reason: MemorySelectionReason
    score_components: Mapping[str, float]
    rendered_text: str
    token_count: int
    source_cursor: int
    exact_match: bool
    lexical_score: float
    confidence: float
    conflict_set_id: UUID | None
    section: str

    @property
    def key(self) -> tuple[MemorySourceKind, UUID, int | None]:
        return (self.source_kind, self.source_id, self.source_revision)


class SnapshotCandidateCollector:
    """Resolves projection hits back to canonical, scoped memory sources."""

    def __init__(
        self,
        *,
        memory_repository: MemoryRepository,
        policy: MemoryPolicy,
        token_counter: _TokenCounter,
        projection_generation: int,
    ) -> None:
        self._memory = memory_repository
        self._policy = policy
        self._tokens = token_counter
        self._generation = projection_generation

    def normalize_hits(
        self,
        hits: tuple[MemorySearchHit, ...],
        *,
        scope: ScopeContract,
        source_watermark_cursor: int,
    ) -> dict[tuple[MemorySourceKind, UUID, int | None], MemorySearchHit]:
        normalized: dict[tuple[MemorySourceKind, UUID, int | None], MemorySearchHit] = {}
        for hit in sorted(hits, key=self._hit_input_key):
            document = hit.document
            if document.source_kind not in {
                MemorySourceKind.CLAIM_REVISION,
                MemorySourceKind.OBSERVATION,
            }:
                continue
            if not document_is_in_scope(
                document,
                scope=scope,
                projection_generation=self._generation,
                source_watermark_cursor=source_watermark_cursor,
            ):
                continue
            key = (document.source_kind, document.source_id, document.source_revision)
            current = normalized.get(key)
            if current is None or self._hit_quality_key(hit) > self._hit_quality_key(current):
                normalized[key] = hit
        return normalized

    def claim_candidates(
        self,
        *,
        scope: ScopeContract,
        query: str,
        source_watermark_cursor: int,
        hits: Mapping[tuple[MemorySourceKind, UUID, int | None], MemorySearchHit],
        at: datetime,
    ) -> tuple[list[SnapshotCandidate], set[UUID]]:
        candidates: list[SnapshotCandidate] = []
        provenance_ids: set[UUID] = set()
        for namespace in readable_namespaces(self._policy, scope):
            claims = self._memory.claims_for_scope(
                namespace=namespace,
                **repository_scope(namespace, scope),
            )
            for claim in sorted(claims, key=self._claim_input_key):
                if not claim_is_in_scope(claim, scope):
                    continue
                revisions = self._memory.revisions_for_claim(claim.id)
                revision = next(
                    (value for value in revisions if value.revision == claim.current_revision),
                    None,
                )
                if revision is None or not self._policy.is_revision_current(revision, at=at):
                    continue
                if not self._policy.scan_content(revision.normalized_text).allowed:
                    continue
                source_observations = self._source_observations(revision)
                if source_observations is None:
                    continue
                if any(
                    observation.source_cursor > source_watermark_cursor
                    or not observation_is_in_scope(observation, namespace, scope)
                    or not self._policy.is_observation_retrievable(observation)
                    for observation in source_observations
                ):
                    continue
                provenance_ids.update(revision.source_observation_ids)
                source_cursor = max(
                    observation.source_cursor for observation in source_observations
                )
                hit = hits.get((MemorySourceKind.CLAIM_REVISION, claim.id, revision.revision))
                lexical_score = hit.lexical_score if hit is not None else 0.0
                exact_match = (
                    hit.exact_match if hit is not None else False
                ) or is_exact_query_match(
                    query,
                    f"{claim.subject} {claim.predicate} {revision.normalized_text}",
                )
                conflict = (
                    claim.status is ClaimStatus.CONFLICTED or claim.conflict_set_id is not None
                )
                candidates.append(
                    self._candidate(
                        source_kind=MemorySourceKind.CLAIM_REVISION,
                        source_id=claim.id,
                        source_revision=revision.revision,
                        namespace=namespace,
                        authority=revision.authority,
                        selection_reason=self._claim_reason(
                            namespace=namespace,
                            exact_match=exact_match,
                            conflict=conflict,
                        ),
                        rendered_text=render_claim(
                            claim=claim,
                            revision=revision,
                            conflict=conflict,
                        ),
                        source_cursor=source_cursor,
                        source_watermark_cursor=source_watermark_cursor,
                        exact_match=exact_match,
                        lexical_score=lexical_score,
                        confidence=revision.confidence,
                        conflict_set_id=claim.conflict_set_id,
                        section=self._claim_section(namespace),
                    )
                )
        return candidates, provenance_ids

    def search_history_candidates(
        self,
        *,
        scope: ScopeContract,
        query: str,
        source_watermark_cursor: int,
        hits: Mapping[tuple[MemorySourceKind, UUID, int | None], MemorySearchHit],
        excluded_observation_ids: set[UUID],
    ) -> list[SnapshotCandidate]:
        candidates: list[SnapshotCandidate] = []
        for key, hit in sorted(hits.items(), key=lambda item: self._hit_input_key(item[1])):
            source_kind, source_id, _source_revision = key
            if source_kind is not MemorySourceKind.OBSERVATION:
                continue
            if source_id in excluded_observation_ids:
                continue
            observation = self._safe_observation(source_id)
            if observation is None:
                continue
            candidate = self._observation_candidate(
                observation,
                scope=scope,
                query=query,
                source_watermark_cursor=source_watermark_cursor,
                lexical_score=hit.lexical_score,
                exact_match=hit.exact_match,
                reason=MemorySelectionReason.LEXICAL_HISTORY,
            )
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def fallback_history_candidates(
        self,
        *,
        scope: ScopeContract,
        query: str,
        source_watermark_cursor: int,
        excluded_observation_ids: set[UUID],
    ) -> list[SnapshotCandidate]:
        observations: dict[UUID, MemoryObservation] = {}
        for namespace in readable_namespaces(self._policy, scope):
            scoped = self._memory.observations_for_scope(
                namespace=namespace,
                limit=_FALLBACK_OBSERVATION_LIMIT,
                newest_first=True,
                retrievable_only=True,
                **repository_scope(namespace, scope),
            )
            for observation in scoped:
                observations[observation.id] = observation
        candidates: list[SnapshotCandidate] = []
        for observation in sorted(
            observations.values(),
            key=lambda value: (-value.source_cursor, str(value.id)),
        ):
            if len(candidates) >= _FALLBACK_OBSERVATION_LIMIT:
                break
            if observation.id in excluded_observation_ids:
                continue
            candidate = self._observation_candidate(
                observation,
                scope=scope,
                query=query,
                source_watermark_cursor=source_watermark_cursor,
                lexical_score=0.0,
                exact_match=is_exact_query_match(query, observation.content),
                reason=MemorySelectionReason.RELATIONAL_FALLBACK,
            )
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _observation_candidate(
        self,
        observation: MemoryObservation,
        *,
        scope: ScopeContract,
        query: str,
        source_watermark_cursor: int,
        lexical_score: float,
        exact_match: bool,
        reason: MemorySelectionReason,
    ) -> SnapshotCandidate | None:
        if (
            observation.source_cursor > source_watermark_cursor
            or not self._policy.can_read_namespace(observation.proposed_namespace, scope)
            or not observation_is_in_scope(
                observation,
                observation.proposed_namespace,
                scope,
            )
            or not self._policy.is_observation_retrievable(observation)
        ):
            return None
        return self._candidate(
            source_kind=MemorySourceKind.OBSERVATION,
            source_id=observation.id,
            source_revision=None,
            namespace=observation.proposed_namespace,
            authority=observation.authority,
            selection_reason=reason,
            rendered_text=render_observation(observation),
            source_cursor=observation.source_cursor,
            source_watermark_cursor=source_watermark_cursor,
            exact_match=(exact_match or is_exact_query_match(query, observation.content)),
            lexical_score=lexical_score,
            confidence=observation.confidence,
            conflict_set_id=None,
            section="history",
        )

    def _candidate(
        self,
        *,
        source_kind: MemorySourceKind,
        source_id: UUID,
        source_revision: int | None,
        namespace: MemoryNamespace | None,
        authority: MemoryAuthority,
        selection_reason: MemorySelectionReason,
        rendered_text: str,
        source_cursor: int,
        source_watermark_cursor: int,
        exact_match: bool,
        lexical_score: float,
        confidence: float,
        conflict_set_id: UUID | None,
        section: str,
    ) -> SnapshotCandidate:
        recency = (
            min(1.0, source_cursor / source_watermark_cursor)
            if source_watermark_cursor > 0
            else 0.0
        )
        scores = {
            "authority": round(
                MEMORY_AUTHORITY_PRECEDENCE[authority] / _MAX_AUTHORITY_PRECEDENCE,
                6,
            ),
            "exact": 1.0 if exact_match else 0.0,
            "lexical": round(lexical_score, 6),
            "recency": round(recency, 6),
            "confidence": round(confidence, 6),
        }
        return SnapshotCandidate(
            source_kind=source_kind,
            source_id=source_id,
            source_revision=source_revision,
            namespace=namespace,
            authority=authority,
            selection_reason=selection_reason,
            score_components=scores,
            rendered_text=rendered_text,
            token_count=self._tokens.count(rendered_text),
            source_cursor=source_cursor,
            exact_match=exact_match,
            lexical_score=lexical_score,
            confidence=confidence,
            conflict_set_id=conflict_set_id,
            section=section,
        )

    def _source_observations(
        self,
        revision: MemoryClaimRevision,
    ) -> tuple[MemoryObservation, ...] | None:
        observations: list[MemoryObservation] = []
        for observation_id in revision.source_observation_ids:
            observation = self._safe_observation(observation_id)
            if observation is None:
                return None
            observations.append(observation)
        return tuple(observations)

    def _safe_observation(self, observation_id: UUID) -> MemoryObservation | None:
        try:
            return self._memory.get_observation(observation_id)
        except MemoryForgottenError:
            return None

    @staticmethod
    def _claim_reason(
        *,
        namespace: MemoryNamespace,
        exact_match: bool,
        conflict: bool,
    ) -> MemorySelectionReason:
        if conflict:
            return MemorySelectionReason.CONFLICT_DISCLOSURE
        if namespace is MemoryNamespace.PROJECT_CANONICAL:
            return (
                MemorySelectionReason.EXACT_CANONICAL
                if exact_match
                else MemorySelectionReason.CANONICAL
            )
        if namespace is MemoryNamespace.USER_PROFILE:
            return (
                MemorySelectionReason.EXACT_PROFILE
                if exact_match
                else MemorySelectionReason.USER_PROFILE
            )
        if namespace is MemoryNamespace.CONVERSATION_DRAFT:
            return MemorySelectionReason.CONVERSATION_DRAFT
        return MemorySelectionReason.LEXICAL_HISTORY

    @staticmethod
    def _claim_section(namespace: MemoryNamespace) -> str:
        return {
            MemoryNamespace.USER_PROFILE: "profile",
            MemoryNamespace.PROJECT_CANONICAL: "canonical",
            MemoryNamespace.CONVERSATION_DRAFT: "draft",
        }.get(namespace, "history")

    @staticmethod
    def _claim_input_key(claim: MemoryClaim) -> tuple[str, str, str, str]:
        return (
            claim.namespace.value,
            claim.subject.casefold(),
            claim.predicate.casefold(),
            str(claim.id),
        )

    @staticmethod
    def _hit_input_key(hit: MemorySearchHit) -> tuple[str, str, int, int, str]:
        document = hit.document
        return (
            document.source_kind.value,
            str(document.source_id),
            document.source_revision or 0,
            document.source_cursor,
            document.content_hash,
        )

    @staticmethod
    def _hit_quality_key(hit: MemorySearchHit) -> tuple[int, float, int, str]:
        return (
            int(hit.exact_match),
            hit.lexical_score,
            hit.document.source_cursor,
            hit.document.content_hash,
        )


__all__ = ["SnapshotCandidate", "SnapshotCandidateCollector"]
