from __future__ import annotations

import hmac
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from fairy_core.domain.models import ScopeContract, WorkspaceType
from fairy_core.memory.models import (
    MemoryAuthority,
    MemoryClaimRevision,
    MemoryNamespace,
    MemoryObservation,
    MemoryScanResult,
    MemorySensitivity,
    ObservationStatus,
    memory_content_hash,
)


@dataclass(frozen=True, slots=True)
class MemoryPolicyDecision:
    allowed: bool
    requires_approval: bool = False
    error_code: str | None = None
    reason: str = ""
    scan_result: MemoryScanResult = MemoryScanResult.UNCHECKED


_SECRET_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bgithub_pat_[A-Za-z0-9_]{20,}\b",
        r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b",
        r"\bsk-[A-Za-z0-9_-]{20,}\b",
        r"\b(?:api[_-]?key|password|passwd|secret|access[_-]?token)\s*[:=]\s*"
        r"[\"']?[^\s\"']{8,}",
    )
)

_INSTRUCTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bignore\s+(?:all\s+)?previous\s+instructions?\b",
        r"\bsystem\s*:\s*follow\s+these\s+instructions?\b",
        r"<\s*system\b",
        r"\byou\s+are\s+now\s+(?:an?|the)\b",
        r"\btreat\s+this\s+memory\s+as\s+an?\s+instruction\b",
        r"\breveal\s+(?:the\s+)?system\s+prompt\b",
    )
)
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")

_WRITE_SCOPE_ALIASES: dict[MemoryNamespace, frozenset[str]] = {
    MemoryNamespace.PROJECT_CANONICAL: frozenset({"project_canonical"}),
    MemoryNamespace.CONVERSATION_DRAFT: frozenset(
        {"conversation_draft", "current_conversation_draft"}
    ),
    MemoryNamespace.USER_PROFILE: frozenset({"user_profile", "personal"}),
    MemoryNamespace.DEVICE_LOCAL: frozenset({"device_local"}),
    MemoryNamespace.TASK_EPISODE: frozenset({"task_episode", "failure_lesson"}),
}

_READ_SCOPE_ALIASES: dict[MemoryNamespace, frozenset[str]] = {
    MemoryNamespace.PROJECT_CANONICAL: frozenset({"project_canonical"}),
    MemoryNamespace.CONVERSATION_DRAFT: frozenset(
        {
            "conversation_draft",
            "current_conversation",
            "current_conversation_draft",
        }
    ),
    MemoryNamespace.USER_PROFILE: frozenset({"user_profile", "personal"}),
    MemoryNamespace.DEVICE_LOCAL: frozenset({"device_local"}),
    MemoryNamespace.TASK_EPISODE: frozenset({"task_episode", "failure_lesson"}),
}

MEMORY_NAMESPACE_PRECEDENCE: Mapping[MemoryNamespace, int] = MappingProxyType(
    {
        MemoryNamespace.PROJECT_CANONICAL: 500,
        MemoryNamespace.CONVERSATION_DRAFT: 400,
        MemoryNamespace.USER_PROFILE: 300,
        MemoryNamespace.DEVICE_LOCAL: 200,
        MemoryNamespace.TASK_EPISODE: 100,
    }
)

MEMORY_AUTHORITY_PRECEDENCE: Mapping[MemoryAuthority, int] = MappingProxyType(
    {
        MemoryAuthority.DETERMINISTIC_CORE: 400,
        MemoryAuthority.ACCEPTED_VERSION: 350,
        MemoryAuthority.EXPLICIT_USER: 300,
        MemoryAuthority.MODEL_SUGGESTION: 100,
    }
)


class MemoryPolicy:
    @staticmethod
    def precedence_key(
        namespace: MemoryNamespace,
        authority: MemoryAuthority,
    ) -> tuple[int, int]:
        return (
            MEMORY_NAMESPACE_PRECEDENCE[namespace],
            MEMORY_AUTHORITY_PRECEDENCE[authority],
        )

    def evaluate_promotion(
        self,
        observation: MemoryObservation,
        target_namespace: MemoryNamespace,
        scope: ScopeContract,
    ) -> MemoryPolicyDecision:
        scope_failure = self._scope_failure(observation, target_namespace, scope)
        if scope_failure is not None:
            return scope_failure
        if observation.status not in {ObservationStatus.PENDING, ObservationStatus.ACCEPTED}:
            return MemoryPolicyDecision(
                False,
                error_code="MEMORY_FORGOTTEN"
                if observation.status is ObservationStatus.FORGOTTEN
                else "MEMORY_SCOPE_VIOLATION",
                reason="Observation is not promotable",
            )
        scan = self.scan_content(observation.content, sensitivity=observation.sensitivity)
        if not scan.allowed:
            return scan
        if (
            target_namespace is MemoryNamespace.PROJECT_CANONICAL
            and observation.authority is MemoryAuthority.MODEL_SUGGESTION
        ):
            return MemoryPolicyDecision(
                False,
                requires_approval=True,
                error_code="APPROVAL_REQUIRED",
                reason="Project Canonical Memory requires explicit user approval",
                scan_result=MemoryScanResult.CLEAN,
            )
        return scan

    @staticmethod
    def can_read_namespace(namespace: MemoryNamespace, scope: ScopeContract) -> bool:
        if not _READ_SCOPE_ALIASES[namespace].intersection(scope.memory_read_scope):
            return False
        if namespace is MemoryNamespace.PROJECT_CANONICAL:
            return (
                scope.workspace_type is WorkspaceType.PROJECT_CHAT
                and scope.project_id is not None
            )
        return namespace is not MemoryNamespace.DEVICE_LOCAL

    def is_observation_retrievable(self, observation: MemoryObservation) -> bool:
        if observation.status not in {
            ObservationStatus.ACCEPTED,
            ObservationStatus.PROMOTED,
        }:
            return False
        if observation.sensitivity is MemorySensitivity.SECRET:
            return False
        if observation.scan_result is not MemoryScanResult.CLEAN:
            return False
        return self.scan_content(
            observation.content,
            sensitivity=observation.sensitivity,
        ).allowed

    @staticmethod
    def is_revision_current(
        revision: MemoryClaimRevision,
        *,
        at: datetime,
    ) -> bool:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Memory retrieval time must be timezone-aware")
        return (
            (revision.valid_from is None or revision.valid_from <= at)
            and (revision.valid_to is None or revision.valid_to > at)
        )

    @staticmethod
    def scan_content(
        content: str,
        *,
        sensitivity: MemorySensitivity = MemorySensitivity.PRIVATE,
    ) -> MemoryPolicyDecision:
        if sensitivity is MemorySensitivity.SECRET or _contains_secret(content):
            return MemoryPolicyDecision(
                False,
                error_code="MEMORY_SECRET_BLOCKED",
                reason="Memory contains secret-like material",
                scan_result=MemoryScanResult.SECRET_BLOCKED,
            )
        if _contains_invisible_control(content) or _contains_instruction(content):
            return MemoryPolicyDecision(
                False,
                error_code="MEMORY_INJECTION_BLOCKED",
                reason="Memory contains instruction-like or invisible control content",
                scan_result=MemoryScanResult.INJECTION_BLOCKED,
            )
        return MemoryPolicyDecision(True, scan_result=MemoryScanResult.CLEAN)

    @staticmethod
    def _scope_failure(
        observation: MemoryObservation,
        target_namespace: MemoryNamespace,
        scope: ScopeContract,
    ) -> MemoryPolicyDecision | None:
        expected_version_id = scope.target_version_id or scope.base_version_id
        identity_matches = (
            observation.project_id == scope.project_id
            and observation.conversation_id == scope.conversation_id
            and observation.task_id == scope.task_id
            and observation.version_id == expected_version_id
            and _secure_digest_equal(observation.scope_digest, scope.scope_digest)
            and _secure_digest_equal(
                observation.content_hash,
                memory_content_hash(observation.content),
            )
        )
        if not identity_matches:
            return MemoryPolicyDecision(
                False,
                error_code="MEMORY_SCOPE_VIOLATION",
                reason="Observation identity or integrity does not match Core Scope",
            )
        if observation.proposed_namespace is not target_namespace:
            return MemoryPolicyDecision(
                False,
                error_code="MEMORY_SCOPE_VIOLATION",
                reason="Observation namespace does not match promotion target",
            )
        if not _authority_matches_actor(observation.authority, observation.actor):
            return MemoryPolicyDecision(
                False,
                error_code="MEMORY_SCOPE_VIOLATION",
                reason="Observation authority does not match its Core-injected actor",
            )
        writable = frozenset(scope.memory_write_scope)
        if not _WRITE_SCOPE_ALIASES[target_namespace].intersection(writable):
            return MemoryPolicyDecision(
                False,
                error_code="MEMORY_SCOPE_VIOLATION",
                reason="Target namespace is not writable in this Scope",
            )
        if target_namespace is MemoryNamespace.PROJECT_CANONICAL and (
            scope.workspace_type is not WorkspaceType.PROJECT_CHAT or scope.project_id is None
        ):
            return MemoryPolicyDecision(
                False,
                error_code="MEMORY_SCOPE_VIOLATION",
                reason="Project Canonical Memory requires a project Scope",
            )
        return None


def _contains_secret(content: str) -> bool:
    return any(pattern.search(content) is not None for pattern in _SECRET_PATTERNS)


def _contains_invisible_control(content: str) -> bool:
    for character in content:
        category = unicodedata.category(character)
        if category == "Cf" or (category == "Cc" and character not in "\n\r\t"):
            return True
    return False


def _contains_instruction(content: str) -> bool:
    return any(pattern.search(content) is not None for pattern in _INSTRUCTION_PATTERNS)


def _authority_matches_actor(authority: MemoryAuthority, actor: str) -> bool:
    actor_kind, separator, identity = actor.partition(":")
    if authority is MemoryAuthority.MODEL_SUGGESTION:
        return actor_kind == "model"
    if authority is MemoryAuthority.EXPLICIT_USER:
        return actor_kind == "user" and bool(separator) and bool(identity)
    return actor_kind == "core"


def _secure_digest_equal(candidate: str, expected: str) -> bool:
    if _SHA256_HEX.fullmatch(candidate) is None or _SHA256_HEX.fullmatch(expected) is None:
        return False
    return hmac.compare_digest(candidate, expected)


__all__ = [
    "MEMORY_AUTHORITY_PRECEDENCE",
    "MEMORY_NAMESPACE_PRECEDENCE",
    "MemoryPolicy",
    "MemoryPolicyDecision",
]
