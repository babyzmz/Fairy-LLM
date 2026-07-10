from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from uuid import UUID

from fairy_core.domain.errors import MemoryForgottenError, MemorySnapshotTooLargeError
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
    MemoryProjectionHealth,
    MemorySearchHit,
    MemorySelectionReason,
    MemorySnapshot,
    MemorySnapshotItem,
    MemorySnapshotStatus,
    MemorySourceKind,
    ProjectionState,
)
from fairy_core.memory.retrieval_ports import MemorySearchIndex

DEFAULT_SNAPSHOT_TOKEN_BUDGET = 2_400
HARD_SNAPSHOT_TOKEN_CEILING = 3_000
USER_PROFILE_TOKEN_BUDGET = 400
PROJECT_CANONICAL_TOKEN_BUDGET = 1_000
CONVERSATION_DRAFT_TOKEN_BUDGET = 500
HISTORY_TOKEN_BUDGET = 500

_SEARCH_LIMIT = 100
_FALLBACK_OBSERVATION_LIMIT = 100
_MAX_QUERY_LENGTH = 10_000
_QUERY_TOKEN = re.compile(r"\w+", re.UNICODE)
_MAX_AUTHORITY_PRECEDENCE = max(MEMORY_AUTHORITY_PRECEDENCE.values())


def _now() -> datetime:
    return datetime.now(UTC)


class Utf8ByteTokenCounter:
    """A stable conservative ceiling that is independent of model tokenizers."""

    @staticmethod
    def count(text: str) -> int:
        return len(text.encode("utf-8", errors="strict"))


@dataclass(frozen=True, slots=True)
class _Candidate:
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


class DeterministicMemorySnapshotBuilder:
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
        self._memory = memory_repository
        self._search = search_index
        self._policy = policy or MemoryPolicy()
        self._tokens = token_counter or Utf8ByteTokenCounter()
        self._clock = clock
        self._policy_version = policy_version.strip()
        self._generation = projection_generation

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

        valid_hits = self._normalize_hits(
            hits,
            scope=scope,
            source_watermark_cursor=source_watermark_cursor,
        )
        claim_candidates, provenance_observation_ids = self._claim_candidates(
            scope=scope,
            query=normalized_query,
            source_watermark_cursor=source_watermark_cursor,
            hits=valid_hits,
            at=at,
        )
        if degraded_reason is None:
            history_candidates = self._search_history_candidates(
                scope=scope,
                query=normalized_query,
                source_watermark_cursor=source_watermark_cursor,
                hits=valid_hits,
                excluded_observation_ids=provenance_observation_ids,
            )
        else:
            history_candidates = self._fallback_history_candidates(
                scope=scope,
                query=normalized_query,
                source_watermark_cursor=source_watermark_cursor,
                excluded_observation_ids=provenance_observation_ids,
            )

        selected = self._select_candidates((*claim_candidates, *history_candidates))
        ordered = sorted(selected, key=self._rank_key)
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
            MemorySnapshotStatus.READY
            if degraded_reason is None
            else MemorySnapshotStatus.DEGRADED
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
            reason = health.last_error_code or (
                f"MEMORY_PROJECTION_{health.state.value.upper()}"
            )
            return health, reason
        return health, None

    def _normalize_hits(
        self,
        hits: tuple[MemorySearchHit, ...],
        *,
        scope: ScopeContract,
        source_watermark_cursor: int,
    ) -> dict[tuple[MemorySourceKind, UUID, int | None], MemorySearchHit]:
        normalized: dict[
            tuple[MemorySourceKind, UUID, int | None], MemorySearchHit
        ] = {}
        for hit in sorted(hits, key=self._hit_input_key):
            document = hit.document
            if not self._document_is_in_scope(
                document_namespace=document.namespace,
                document_project_id=document.project_id,
                document_conversation_id=document.conversation_id,
                document_task_id=document.task_id,
                document_version_id=document.version_id,
                projection_generation=document.projection_generation,
                expected_projection_generation=self._generation,
                source_cursor=document.source_cursor,
                scope=scope,
                source_watermark_cursor=source_watermark_cursor,
            ):
                continue
            key = (document.source_kind, document.source_id, document.source_revision)
            current = normalized.get(key)
            if current is None or self._hit_quality_key(hit) > self._hit_quality_key(current):
                normalized[key] = hit
        return normalized

    def _claim_candidates(
        self,
        *,
        scope: ScopeContract,
        query: str,
        source_watermark_cursor: int,
        hits: Mapping[tuple[MemorySourceKind, UUID, int | None], MemorySearchHit],
        at: datetime,
    ) -> tuple[list[_Candidate], set[UUID]]:
        candidates: list[_Candidate] = []
        provenance_ids: set[UUID] = set()
        for namespace in self._readable_namespaces(scope):
            claims = self._memory.claims_for_scope(
                namespace=namespace,
                **self._repository_scope(namespace, scope),
            )
            for claim in sorted(claims, key=self._claim_input_key):
                if not self._claim_is_in_scope(claim, scope):
                    continue
                revisions = self._memory.revisions_for_claim(claim.id)
                revision = next(
                    (
                        value
                        for value in revisions
                        if value.revision == claim.current_revision
                    ),
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
                    or not self._observation_is_in_scope(observation, namespace, scope)
                    or not self._policy.is_observation_retrievable(observation)
                    for observation in source_observations
                ):
                    continue
                provenance_ids.update(revision.source_observation_ids)
                source_cursor = max(
                    observation.source_cursor for observation in source_observations
                )
                hit = hits.get(
                    (MemorySourceKind.CLAIM_REVISION, claim.id, revision.revision)
                )
                lexical_score = hit.lexical_score if hit is not None else 0.0
                exact_match = (
                    hit.exact_match if hit is not None else False
                ) or self._is_exact_query_match(
                    query,
                    f"{claim.subject} {claim.predicate} {revision.normalized_text}",
                )
                conflict = (
                    claim.status is ClaimStatus.CONFLICTED
                    or claim.conflict_set_id is not None
                )
                reason = self._claim_reason(
                    namespace=namespace,
                    exact_match=exact_match,
                    conflict=conflict,
                )
                rendered = self._render_claim(
                    claim=claim,
                    revision=revision,
                    conflict=conflict,
                )
                candidates.append(
                    self._candidate(
                        source_kind=MemorySourceKind.CLAIM_REVISION,
                        source_id=claim.id,
                        source_revision=revision.revision,
                        namespace=namespace,
                        authority=revision.authority,
                        selection_reason=reason,
                        rendered_text=rendered,
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

    def _search_history_candidates(
        self,
        *,
        scope: ScopeContract,
        query: str,
        source_watermark_cursor: int,
        hits: Mapping[tuple[MemorySourceKind, UUID, int | None], MemorySearchHit],
        excluded_observation_ids: set[UUID],
    ) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        for key, hit in sorted(hits.items(), key=lambda item: self._hit_input_key(item[1])):
            source_kind, source_id, _source_revision = key
            if source_kind is MemorySourceKind.CLAIM_REVISION:
                continue
            if source_kind is MemorySourceKind.OBSERVATION:
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
                continue
            document = hit.document
            if not self._policy.scan_content(document.normalized_text).allowed:
                continue
            rendered = self._render_projected_event(document.normalized_text)
            candidates.append(
                self._candidate(
                    source_kind=MemorySourceKind.DOMAIN_EVENT,
                    source_id=document.source_id,
                    source_revision=None,
                    namespace=document.namespace,
                    authority=MemoryAuthority.DETERMINISTIC_CORE,
                    selection_reason=MemorySelectionReason.LEXICAL_HISTORY,
                    rendered_text=rendered,
                    source_cursor=document.source_cursor,
                    source_watermark_cursor=source_watermark_cursor,
                    exact_match=(
                        hit.exact_match
                        or self._is_exact_query_match(query, document.normalized_text)
                    ),
                    lexical_score=hit.lexical_score,
                    confidence=1.0,
                    conflict_set_id=None,
                    section="history",
                )
            )
        return candidates

    def _fallback_history_candidates(
        self,
        *,
        scope: ScopeContract,
        query: str,
        source_watermark_cursor: int,
        excluded_observation_ids: set[UUID],
    ) -> list[_Candidate]:
        observations: dict[UUID, MemoryObservation] = {}
        for namespace in self._readable_namespaces(scope):
            scoped = self._memory.observations_for_scope(
                namespace=namespace,
                limit=_FALLBACK_OBSERVATION_LIMIT,
                newest_first=True,
                retrievable_only=True,
                **self._repository_scope(namespace, scope),
            )
            for observation in scoped:
                observations[observation.id] = observation
        candidates: list[_Candidate] = []
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
                exact_match=self._is_exact_query_match(query, observation.content),
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
    ) -> _Candidate | None:
        if (
            observation.source_cursor > source_watermark_cursor
            or not self._policy.can_read_namespace(observation.proposed_namespace, scope)
            or not self._observation_is_in_scope(
                observation,
                observation.proposed_namespace,
                scope,
            )
            or not self._policy.is_observation_retrievable(observation)
        ):
            return None
        rendered = self._render_observation(observation)
        return self._candidate(
            source_kind=MemorySourceKind.OBSERVATION,
            source_id=observation.id,
            source_revision=None,
            namespace=observation.proposed_namespace,
            authority=observation.authority,
            selection_reason=reason,
            rendered_text=rendered,
            source_cursor=observation.source_cursor,
            source_watermark_cursor=source_watermark_cursor,
            exact_match=(
                exact_match
                or self._is_exact_query_match(query, observation.content)
            ),
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
    ) -> _Candidate:
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
        return _Candidate(
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

    def _select_candidates(self, candidates: Iterable[_Candidate]) -> list[_Candidate]:
        deduplicated: dict[
            tuple[MemorySourceKind, UUID, int | None], _Candidate
        ] = {}
        for candidate in sorted(candidates, key=self._rank_key):
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
            key=self._rank_key,
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
        history_allocation = min(remaining, HISTORY_TOKEN_BUDGET + (draft_allocation - used))
        chosen, _used = self._fit(history_candidates, history_allocation)
        selected.update((candidate.key, candidate) for candidate in chosen)
        return list(selected.values())

    @staticmethod
    def _fit(
        candidates: list[_Candidate],
        budget: int,
    ) -> tuple[list[_Candidate], int]:
        selected: list[_Candidate] = []
        used = 0
        for candidate in sorted(candidates, key=DeterministicMemorySnapshotBuilder._rank_key):
            if used + candidate.token_count <= budget:
                selected.append(candidate)
                used += candidate.token_count
        return selected, used

    @staticmethod
    def _unselected_section(
        candidates: list[_Candidate],
        selected: Mapping[tuple[MemorySourceKind, UUID, int | None], _Candidate],
        section: str,
    ) -> list[_Candidate]:
        return [
            candidate
            for candidate in candidates
            if candidate.section == section and candidate.key not in selected
        ]

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

    def _readable_namespaces(self, scope: ScopeContract) -> tuple[MemoryNamespace, ...]:
        order = (
            MemoryNamespace.USER_PROFILE,
            MemoryNamespace.PROJECT_CANONICAL,
            MemoryNamespace.CONVERSATION_DRAFT,
            MemoryNamespace.TASK_EPISODE,
            MemoryNamespace.DEVICE_LOCAL,
        )
        return tuple(
            namespace
            for namespace in order
            if self._policy.can_read_namespace(namespace, scope)
            and namespace is not MemoryNamespace.DEVICE_LOCAL
        )

    @staticmethod
    def _repository_scope(
        namespace: MemoryNamespace,
        scope: ScopeContract,
    ) -> dict[str, UUID]:
        if namespace is MemoryNamespace.PROJECT_CANONICAL:
            return {"project_id": scope.project_id} if scope.project_id is not None else {}
        if namespace is MemoryNamespace.CONVERSATION_DRAFT:
            return {"conversation_id": scope.conversation_id}
        if namespace is MemoryNamespace.TASK_EPISODE:
            return {"task_id": scope.task_id}
        return {}

    @staticmethod
    def _claim_is_in_scope(claim: MemoryClaim, scope: ScopeContract) -> bool:
        if claim.status not in {ClaimStatus.ACTIVE, ClaimStatus.CONFLICTED}:
            return False
        current_version = scope.target_version_id or scope.base_version_id
        if claim.version_id is not None and claim.version_id != current_version:
            return False
        if claim.namespace is MemoryNamespace.PROJECT_CANONICAL:
            return scope.project_id is not None and claim.project_id == scope.project_id
        if claim.namespace is MemoryNamespace.CONVERSATION_DRAFT:
            return claim.conversation_id == scope.conversation_id
        if claim.namespace is MemoryNamespace.USER_PROFILE:
            return all(
                value is None
                for value in (
                    claim.project_id,
                    claim.conversation_id,
                    claim.task_id,
                    claim.version_id,
                    claim.device_id,
                )
            )
        if claim.namespace is MemoryNamespace.TASK_EPISODE:
            return claim.task_id == scope.task_id
        return False

    @staticmethod
    def _observation_is_in_scope(
        observation: MemoryObservation,
        namespace: MemoryNamespace,
        scope: ScopeContract,
    ) -> bool:
        if namespace is MemoryNamespace.PROJECT_CANONICAL:
            return observation.project_id == scope.project_id
        if namespace is MemoryNamespace.CONVERSATION_DRAFT:
            return observation.conversation_id == scope.conversation_id
        if namespace is MemoryNamespace.TASK_EPISODE:
            return observation.task_id == scope.task_id
        return namespace is MemoryNamespace.USER_PROFILE

    @staticmethod
    def _document_is_in_scope(
        *,
        document_namespace: MemoryNamespace | None,
        document_project_id: UUID | None,
        document_conversation_id: UUID | None,
        document_task_id: UUID | None,
        document_version_id: UUID | None,
        projection_generation: int,
        expected_projection_generation: int,
        source_cursor: int,
        scope: ScopeContract,
        source_watermark_cursor: int,
    ) -> bool:
        if (
            projection_generation != expected_projection_generation
            or source_cursor > source_watermark_cursor
        ):
            return False
        del document_version_id
        if document_namespace is MemoryNamespace.PROJECT_CANONICAL:
            return scope.project_id is not None and document_project_id == scope.project_id
        if document_namespace is MemoryNamespace.CONVERSATION_DRAFT:
            return document_conversation_id == scope.conversation_id
        if document_namespace is MemoryNamespace.USER_PROFILE:
            return (
                document_project_id is None
                and document_conversation_id is None
                and document_task_id is None
            )
        if document_namespace is MemoryNamespace.TASK_EPISODE:
            return document_task_id == scope.task_id
        if document_namespace is MemoryNamespace.DEVICE_LOCAL:
            return False
        return (
            document_project_id in {None, scope.project_id}
            and document_conversation_id in {None, scope.conversation_id}
            and document_task_id in {None, scope.task_id}
        )

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
    def _render_claim(
        *,
        claim: MemoryClaim,
        revision: MemoryClaimRevision,
        conflict: bool,
    ) -> str:
        conflict_line = "\nCONFLICT: live alternative; do not silently resolve." if conflict else ""
        return (
            '<memory-source kind="claim_revision" role="data" '
            f'namespace="{claim.namespace.value}" authority="{revision.authority.value}">'
            f"{conflict_line}\nsubject: {escape(claim.subject, quote=True)}"
            f"\npredicate: {escape(claim.predicate, quote=True)}"
            f"\nvalue: {escape(revision.normalized_text, quote=True)}"
            "\n</memory-source>"
        )

    @staticmethod
    def _render_observation(observation: MemoryObservation) -> str:
        return (
            '<memory-source kind="observation" role="untrusted-data" '
            f'namespace="{observation.proposed_namespace.value}" '
            f'authority="{observation.authority.value}">\n'
            f"{escape(observation.content, quote=True)}\n"
            "</memory-source>"
        )

    @staticmethod
    def _render_projected_event(content: str) -> str:
        return (
            '<memory-source kind="domain_event" role="untrusted-data" '
            'authority="deterministic_core">\n'
            f"{escape(content, quote=True)}\n"
            "</memory-source>"
        )

    @staticmethod
    def _is_exact_query_match(query: str, content: str) -> bool:
        normalized_query = query.casefold().strip()
        normalized_content = content.casefold()
        if normalized_query in normalized_content:
            return True
        query_tokens = set(_QUERY_TOKEN.findall(normalized_query))
        content_tokens = set(_QUERY_TOKEN.findall(normalized_content))
        return bool(query_tokens) and query_tokens.issubset(content_tokens)

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

    @staticmethod
    def _rank_key(candidate: _Candidate) -> tuple[object, ...]:
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
    "DeterministicMemorySnapshotBuilder",
    "Utf8ByteTokenCounter",
]
