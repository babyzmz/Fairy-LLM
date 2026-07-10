from __future__ import annotations

from uuid import UUID

from fairy_core.domain.models import ScopeContract
from fairy_core.memory.models import (
    ClaimStatus,
    MemoryClaim,
    MemoryNamespace,
    MemoryObservation,
)
from fairy_core.memory.policy import MemoryPolicy
from fairy_core.memory.retrieval_models import MemorySearchDocument


def readable_namespaces(
    policy: MemoryPolicy,
    scope: ScopeContract,
) -> tuple[MemoryNamespace, ...]:
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
        if policy.can_read_namespace(namespace, scope)
        and namespace is not MemoryNamespace.DEVICE_LOCAL
    )


def repository_scope(
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


def claim_is_in_scope(claim: MemoryClaim, scope: ScopeContract) -> bool:
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


def observation_is_in_scope(
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


def document_is_in_scope(
    document: MemorySearchDocument,
    *,
    scope: ScopeContract,
    projection_generation: int,
    source_watermark_cursor: int,
) -> bool:
    if (
        document.projection_generation != projection_generation
        or document.source_cursor > source_watermark_cursor
    ):
        return False
    if document.namespace is MemoryNamespace.PROJECT_CANONICAL:
        return scope.project_id is not None and document.project_id == scope.project_id
    if document.namespace is MemoryNamespace.CONVERSATION_DRAFT:
        return document.conversation_id == scope.conversation_id
    if document.namespace is MemoryNamespace.USER_PROFILE:
        return (
            document.project_id is None
            and document.conversation_id is None
            and document.task_id is None
        )
    if document.namespace is MemoryNamespace.TASK_EPISODE:
        return document.task_id == scope.task_id
    return False


__all__ = [
    "claim_is_in_scope",
    "document_is_in_scope",
    "observation_is_in_scope",
    "readable_namespaces",
    "repository_scope",
]
