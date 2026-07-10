from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from fairy_core.commanding.ports import CommandLedger
from fairy_core.memory.models import (
    ClaimStatus,
    MemoryClaim,
    MemoryClaimRevision,
    MemoryNamespace,
    MemoryObservation,
)
from fairy_core.memory.policy import MemoryPolicy
from fairy_core.memory.ports import MemoryRepository
from fairy_core.memory.retrieval_models import (
    MemoryProjectionHealth,
    MemorySearchDocument,
    MemorySourceKind,
)
from fairy_core.memory.retrieval_ports import MemoryProjectionWriter


def _now() -> datetime:
    return datetime.now(UTC)


class LexicalProjectionRefresher:
    def __init__(
        self,
        *,
        memory_repository: MemoryRepository,
        projection_writer: MemoryProjectionWriter,
        command_ledger: CommandLedger,
        memory_policy: MemoryPolicy | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._memory = memory_repository
        self._writer = projection_writer
        self._commands = command_ledger
        self._policy = memory_policy or MemoryPolicy()
        self._clock = clock

    def refresh(self, generation: int = 1) -> MemoryProjectionHealth:
        if generation < 1:
            raise ValueError("generation must be positive")
        at = self._clock()
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Projection clock must return a timezone-aware datetime")

        observations = self._memory.observations_for_projection()
        observations_by_id = {observation.id: observation for observation in observations}
        documents: list[MemorySearchDocument] = []

        for observation in sorted(observations, key=lambda value: str(value.id)):
            self._writer.remove_source(
                source_kind=MemorySourceKind.OBSERVATION,
                source_id=observation.id,
            )
            document = self._observation_document(observation, generation=generation)
            if document is not None:
                documents.append(document)

        claims = self._memory.claims_for_projection()
        for claim in sorted(claims, key=lambda value: str(value.id)):
            self._writer.remove_source(
                source_kind=MemorySourceKind.CLAIM_REVISION,
                source_id=claim.id,
            )
            document = self._claim_document(
                claim,
                observations_by_id=observations_by_id,
                generation=generation,
                at=at,
            )
            if document is not None:
                documents.append(document)

        documents.sort(
            key=lambda value: (
                value.source_kind.value,
                str(value.source_id),
                value.source_revision or 0,
            )
        )
        self._writer.upsert_documents(tuple(documents))
        return self._writer.advance_checkpoint(
            generation=generation,
            source_watermark_cursor=self._commands.current_cursor(),
        )

    def _observation_document(
        self,
        observation: MemoryObservation,
        *,
        generation: int,
    ) -> MemorySearchDocument | None:
        if not self._policy.is_observation_retrievable(observation):
            return None
        project_id, conversation_id, task_id, version_id = self._projection_scope(
            namespace=observation.proposed_namespace,
            project_id=observation.project_id,
            conversation_id=observation.conversation_id,
            task_id=observation.task_id,
            version_id=observation.version_id,
        )
        return MemorySearchDocument.create(
            source_kind=MemorySourceKind.OBSERVATION,
            source_id=observation.id,
            source_revision=None,
            namespace=observation.proposed_namespace,
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            version_id=version_id,
            language="und",
            normalized_text=observation.content,
            source_cursor=observation.source_cursor,
            projection_generation=generation,
        )

    def _claim_document(
        self,
        claim: MemoryClaim,
        *,
        observations_by_id: dict[UUID, MemoryObservation],
        generation: int,
        at: datetime,
    ) -> MemorySearchDocument | None:
        if claim.status not in {ClaimStatus.ACTIVE, ClaimStatus.CONFLICTED}:
            return None
        revision = self._current_revision(claim)
        if revision is None or not self._policy.is_revision_current(revision, at=at):
            return None
        if not self._policy.scan_content(revision.normalized_text).allowed:
            return None
        source_observations = [
            observations_by_id.get(observation_id)
            for observation_id in revision.source_observation_ids
        ]
        if any(
            observation is None
            or not self._policy.is_observation_retrievable(observation)
            or not self._observation_supports_claim(observation, claim)
            for observation in source_observations
        ):
            return None
        typed_observations = [
            observation
            for observation in source_observations
            if observation is not None
        ]
        source_cursor = max(observation.source_cursor for observation in typed_observations)
        project_id, conversation_id, task_id, version_id = self._projection_scope(
            namespace=claim.namespace,
            project_id=claim.project_id,
            conversation_id=claim.conversation_id,
            task_id=claim.task_id,
            version_id=claim.version_id,
        )
        return MemorySearchDocument.create(
            source_kind=MemorySourceKind.CLAIM_REVISION,
            source_id=claim.id,
            source_revision=revision.revision,
            namespace=claim.namespace,
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            version_id=version_id,
            language="und",
            normalized_text=(
                f"{claim.subject} {claim.predicate} {revision.normalized_text}"
            ),
            source_cursor=source_cursor,
            projection_generation=generation,
        )

    def _current_revision(self, claim: MemoryClaim) -> MemoryClaimRevision | None:
        return next(
            (
                revision
                for revision in self._memory.revisions_for_claim(claim.id)
                if revision.revision == claim.current_revision
            ),
            None,
        )

    @staticmethod
    def _projection_scope(
        *,
        namespace: MemoryNamespace,
        project_id: UUID | None,
        conversation_id: UUID | None,
        task_id: UUID | None,
        version_id: UUID | None,
    ) -> tuple[UUID | None, UUID | None, UUID | None, UUID | None]:
        if namespace is MemoryNamespace.USER_PROFILE:
            return None, None, None, None
        if namespace is MemoryNamespace.PROJECT_CANONICAL:
            return project_id, None, None, version_id
        if namespace is MemoryNamespace.CONVERSATION_DRAFT:
            return project_id, conversation_id, None, version_id
        if namespace is MemoryNamespace.TASK_EPISODE:
            return project_id, conversation_id, task_id, version_id
        return project_id, conversation_id, task_id, version_id

    @staticmethod
    def _observation_supports_claim(
        observation: MemoryObservation,
        claim: MemoryClaim,
    ) -> bool:
        if claim.namespace is MemoryNamespace.PROJECT_CANONICAL:
            return observation.project_id == claim.project_id
        if claim.namespace is MemoryNamespace.CONVERSATION_DRAFT:
            return observation.conversation_id == claim.conversation_id
        if claim.namespace is MemoryNamespace.TASK_EPISODE:
            return observation.task_id == claim.task_id
        return claim.namespace is MemoryNamespace.USER_PROFILE


__all__ = ["LexicalProjectionRefresher"]
