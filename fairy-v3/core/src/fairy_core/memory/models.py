from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID

from fairy_core.domain.errors import (
    InvalidTransitionError,
    MemoryConflictError,
    MemoryForgottenError,
)
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import ScopeContract


def _now() -> datetime:
    return datetime.now(UTC)


class MemoryNamespace(StrEnum):
    PROJECT_CANONICAL = "project_canonical"
    CONVERSATION_DRAFT = "conversation_draft"
    USER_PROFILE = "user_profile"
    DEVICE_LOCAL = "device_local"
    TASK_EPISODE = "task_episode"


class MemoryAuthority(StrEnum):
    DETERMINISTIC_CORE = "deterministic_core"
    EXPLICIT_USER = "explicit_user"
    ACCEPTED_VERSION = "accepted_version"
    MODEL_SUGGESTION = "model_suggestion"


class ObservationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PROMOTED = "promoted"
    FORGOTTEN = "forgotten"


class ClaimStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    CONFLICTED = "conflicted"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    REJECTED = "rejected"
    FORGOTTEN = "forgotten"


class MemorySensitivity(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    SECRET = "secret"


class MemoryScanResult(StrEnum):
    UNCHECKED = "unchecked"
    CLEAN = "clean"
    INJECTION_BLOCKED = "injection_blocked"
    SECRET_BLOCKED = "secret_blocked"


class MemorySourceType(StrEnum):
    USER_MESSAGE = "user_message"
    CORE_EVENT = "core_event"
    COMMAND_RESULT = "command_result"
    ACCEPTED_ARTIFACT = "accepted_artifact"
    EXPLICIT_USER_ACTION = "explicit_user_action"
    MODEL_SUGGESTION = "model_suggestion"


class MemoryTargetKind(StrEnum):
    OBSERVATION = "observation"
    CLAIM = "claim"
    EPISODE = "episode"
    SNAPSHOT = "snapshot"


CLAIM_TRANSITIONS: Mapping[ClaimStatus, frozenset[ClaimStatus]] = MappingProxyType(
    {
        ClaimStatus.CANDIDATE: frozenset(
            {
                ClaimStatus.ACTIVE,
                ClaimStatus.CONFLICTED,
                ClaimStatus.SUPERSEDED,
                ClaimStatus.EXPIRED,
                ClaimStatus.REJECTED,
                ClaimStatus.FORGOTTEN,
            }
        ),
        ClaimStatus.ACTIVE: frozenset(
            {
                ClaimStatus.CONFLICTED,
                ClaimStatus.SUPERSEDED,
                ClaimStatus.EXPIRED,
                ClaimStatus.FORGOTTEN,
            }
        ),
        ClaimStatus.CONFLICTED: frozenset(
            {
                ClaimStatus.ACTIVE,
                ClaimStatus.SUPERSEDED,
                ClaimStatus.EXPIRED,
                ClaimStatus.FORGOTTEN,
            }
        ),
        ClaimStatus.SUPERSEDED: frozenset({ClaimStatus.FORGOTTEN}),
        ClaimStatus.EXPIRED: frozenset(
            {
                ClaimStatus.ACTIVE,
                ClaimStatus.CONFLICTED,
                ClaimStatus.SUPERSEDED,
                ClaimStatus.FORGOTTEN,
            }
        ),
        ClaimStatus.REJECTED: frozenset({ClaimStatus.FORGOTTEN}),
        ClaimStatus.FORGOTTEN: frozenset(),
    }
)


_OBSERVATION_TRANSITIONS: Mapping[ObservationStatus, frozenset[ObservationStatus]] = (
    MappingProxyType(
        {
            ObservationStatus.PENDING: frozenset(
                {
                    ObservationStatus.ACCEPTED,
                    ObservationStatus.REJECTED,
                    ObservationStatus.FORGOTTEN,
                }
            ),
            ObservationStatus.ACCEPTED: frozenset(
                {
                    ObservationStatus.PROMOTED,
                    ObservationStatus.REJECTED,
                    ObservationStatus.FORGOTTEN,
                }
            ),
            ObservationStatus.PROMOTED: frozenset({ObservationStatus.FORGOTTEN}),
            ObservationStatus.REJECTED: frozenset({ObservationStatus.FORGOTTEN}),
            ObservationStatus.FORGOTTEN: frozenset(),
        }
    )
)


@dataclass(frozen=True, slots=True)
class MemoryObservation:
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    scope_digest: str
    source_event_id: UUID
    source_cursor: int
    source_type: MemorySourceType
    content: str
    content_hash: str
    proposed_namespace: MemoryNamespace
    authority: MemoryAuthority
    confidence: float
    sensitivity: MemorySensitivity
    scan_result: MemoryScanResult
    status: ObservationStatus
    actor: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        scope: ScopeContract,
        source_event_id: UUID,
        source_cursor: int,
        source_type: MemorySourceType | str,
        content: str,
        proposed_namespace: MemoryNamespace,
        authority: MemoryAuthority,
        confidence: float,
        sensitivity: MemorySensitivity,
        actor: str,
    ) -> MemoryObservation:
        if source_cursor < 1:
            raise ValueError("source_cursor must be positive")
        if not content.strip():
            raise ValueError("memory content is required")
        normalized_actor = actor.strip()
        if not normalized_actor:
            raise ValueError("memory actor is required")
        _validate_confidence(confidence)
        version_id = scope.target_version_id or scope.base_version_id
        return cls(
            id=new_id(),
            project_id=scope.project_id,
            conversation_id=scope.conversation_id,
            task_id=scope.task_id,
            version_id=version_id,
            scope_digest=scope.scope_digest,
            source_event_id=source_event_id,
            source_cursor=source_cursor,
            source_type=MemorySourceType(source_type),
            content=content,
            content_hash=memory_content_hash(content),
            proposed_namespace=proposed_namespace,
            authority=authority,
            confidence=confidence,
            sensitivity=sensitivity,
            scan_result=MemoryScanResult.UNCHECKED,
            status=ObservationStatus.PENDING,
            actor=normalized_actor,
            created_at=_now(),
        )

    def transition_to(
        self,
        status: ObservationStatus,
        *,
        scan_result: MemoryScanResult | None = None,
    ) -> MemoryObservation:
        if status not in _OBSERVATION_TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"cannot transition MemoryObservation from {self.status} to {status}"
            )
        return replace(
            self,
            status=status,
            scan_result=scan_result if scan_result is not None else self.scan_result,
        )


@dataclass(slots=True, init=False)
class MemoryClaim:
    _id: UUID
    _namespace: MemoryNamespace
    _project_id: UUID | None
    _conversation_id: UUID | None
    _task_id: UUID | None
    _version_id: UUID | None
    _device_id: str | None
    _subject: str
    _predicate: str
    _current_revision: int
    _conflict_set_id: UUID | None
    _status: ClaimStatus
    _created_at: datetime
    _updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        namespace: MemoryNamespace,
        subject: str,
        predicate: str,
        project_id: UUID | None = None,
        conversation_id: UUID | None = None,
        task_id: UUID | None = None,
        version_id: UUID | None = None,
        device_id: str | None = None,
    ) -> MemoryClaim:
        now = _now()
        return cls.restore(
            id=new_id(),
            namespace=namespace,
            subject=subject,
            predicate=predicate,
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            version_id=version_id,
            device_id=device_id,
            current_revision=0,
            conflict_set_id=None,
            status=ClaimStatus.CANDIDATE,
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def restore(
        cls,
        *,
        id: UUID,
        namespace: MemoryNamespace,
        subject: str,
        predicate: str,
        project_id: UUID | None,
        conversation_id: UUID | None,
        task_id: UUID | None,
        version_id: UUID | None,
        device_id: str | None,
        current_revision: int,
        conflict_set_id: UUID | None,
        status: ClaimStatus,
        created_at: datetime,
        updated_at: datetime,
    ) -> MemoryClaim:
        normalized_subject = subject.strip()
        normalized_predicate = predicate.strip()
        if not normalized_subject or not normalized_predicate:
            raise ValueError("memory claim subject and predicate are required")
        if current_revision < 0:
            raise ValueError("current_revision cannot be negative")
        _validate_claim_scope(
            namespace=namespace,
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            version_id=version_id,
            device_id=device_id,
        )
        claim = cls.__new__(cls)
        claim._id = id
        claim._namespace = namespace
        claim._project_id = project_id
        claim._conversation_id = conversation_id
        claim._task_id = task_id
        claim._version_id = version_id
        claim._device_id = device_id.strip() if device_id is not None else None
        claim._subject = normalized_subject
        claim._predicate = normalized_predicate
        claim._current_revision = current_revision
        claim._conflict_set_id = conflict_set_id
        claim._status = status
        claim._created_at = created_at
        claim._updated_at = updated_at
        return claim

    @property
    def id(self) -> UUID:
        return self._id

    @property
    def namespace(self) -> MemoryNamespace:
        return self._namespace

    @property
    def project_id(self) -> UUID | None:
        return self._project_id

    @property
    def conversation_id(self) -> UUID | None:
        return self._conversation_id

    @property
    def task_id(self) -> UUID | None:
        return self._task_id

    @property
    def version_id(self) -> UUID | None:
        return self._version_id

    @property
    def device_id(self) -> str | None:
        return self._device_id

    @property
    def subject(self) -> str:
        return self._subject

    @property
    def predicate(self) -> str:
        return self._predicate

    @property
    def current_revision(self) -> int:
        return self._current_revision

    @property
    def conflict_set_id(self) -> UUID | None:
        return self._conflict_set_id

    @property
    def status(self) -> ClaimStatus:
        return self._status

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    def record_revision(self, revision: MemoryClaimRevision) -> None:
        if self._status is ClaimStatus.FORGOTTEN:
            raise MemoryForgottenError("memory claim has been forgotten")
        expected = self._current_revision + 1
        if revision.claim_id != self._id or revision.revision != expected:
            raise MemoryConflictError(
                f"expected revision {expected} for Claim {self._id}, got {revision.revision}"
            )
        expected_superseded = self._current_revision or None
        if revision.supersedes_revision != expected_superseded:
            raise MemoryConflictError("claim revision does not supersede the current revision")
        self._current_revision = revision.revision
        self._updated_at = _now()

    def transition_to(
        self,
        status: ClaimStatus,
        *,
        conflict_set_id: UUID | None = None,
    ) -> None:
        if status not in CLAIM_TRANSITIONS[self._status]:
            raise InvalidTransitionError(
                f"cannot transition MemoryClaim from {self._status} to {status}"
            )
        if status is ClaimStatus.CONFLICTED:
            self._conflict_set_id = conflict_set_id or self._conflict_set_id or new_id()
        self._status = status
        self._updated_at = _now()


@dataclass(frozen=True, slots=True)
class MemoryClaimRevision:
    claim_id: UUID
    revision: int
    _value_json: str = field(repr=False)
    normalized_text: str
    source_observation_ids: tuple[UUID, ...]
    source_event_ids: tuple[UUID, ...]
    authority: MemoryAuthority
    confidence: float
    valid_from: datetime | None
    valid_to: datetime | None
    recorded_at: datetime
    actor: str
    supersedes_revision: int | None

    @classmethod
    def create(
        cls,
        *,
        claim_id: UUID,
        revision: int,
        value: Any,
        normalized_text: str,
        source_observation_ids: tuple[UUID, ...],
        source_event_ids: tuple[UUID, ...],
        authority: MemoryAuthority,
        confidence: float,
        actor: str,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        recorded_at: datetime | None = None,
        supersedes_revision: int | None = None,
    ) -> MemoryClaimRevision:
        if revision < 1:
            raise ValueError("revision must be positive")
        normalized = normalized_text.strip()
        if not normalized:
            raise ValueError("normalized_text is required")
        if not source_observation_ids or not source_event_ids:
            raise ValueError("claim revision requires Observation and event provenance")
        normalized_actor = actor.strip()
        if not normalized_actor:
            raise ValueError("memory actor is required")
        _validate_confidence(confidence)
        if valid_from is not None:
            _validate_aware_datetime(valid_from, "valid_from")
        if valid_to is not None:
            _validate_aware_datetime(valid_to, "valid_to")
        if valid_from is not None and valid_to is not None and valid_to <= valid_from:
            raise ValueError("valid_to must be after valid_from")
        if supersedes_revision is not None and not 1 <= supersedes_revision < revision:
            raise ValueError("supersedes_revision must identify an earlier revision")
        try:
            value_json = json.dumps(
                value,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as error:
            raise ValueError("memory Claim value must be valid JSON") from error
        timestamp = recorded_at or _now()
        _validate_aware_datetime(timestamp, "recorded_at")
        return cls(
            claim_id=claim_id,
            revision=revision,
            _value_json=value_json,
            normalized_text=normalized,
            source_observation_ids=tuple(source_observation_ids),
            source_event_ids=tuple(source_event_ids),
            authority=authority,
            confidence=confidence,
            valid_from=valid_from,
            valid_to=valid_to,
            recorded_at=timestamp,
            actor=normalized_actor,
            supersedes_revision=supersedes_revision,
        )

    @property
    def value(self) -> Any:
        return json.loads(self._value_json)

    @property
    def value_json(self) -> str:
        return self._value_json


@dataclass(frozen=True, slots=True)
class MemoryTombstone:
    id: UUID
    target_kind: MemoryTargetKind
    target_id: UUID
    reason: str
    actor: str
    source_event_id: UUID
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        target_kind: MemoryTargetKind,
        target_id: UUID,
        reason: str,
        actor: str,
        source_event_id: UUID,
    ) -> MemoryTombstone:
        normalized_reason = reason.strip()
        normalized_actor = actor.strip()
        if not normalized_reason or not normalized_actor:
            raise ValueError("tombstone reason and actor are required")
        return cls(
            id=new_id(),
            target_kind=target_kind,
            target_id=target_id,
            reason=normalized_reason,
            actor=normalized_actor,
            source_event_id=source_event_id,
            created_at=_now(),
        )


def memory_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="strict")).hexdigest()


def _validate_confidence(confidence: float) -> None:
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")


def _validate_aware_datetime(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _validate_claim_scope(
    *,
    namespace: MemoryNamespace,
    project_id: UUID | None,
    conversation_id: UUID | None,
    task_id: UUID | None,
    version_id: UUID | None,
    device_id: str | None,
) -> None:
    if namespace is MemoryNamespace.PROJECT_CANONICAL and project_id is None:
        raise ValueError("project_canonical Claim requires project_id")
    if namespace is MemoryNamespace.CONVERSATION_DRAFT and conversation_id is None:
        raise ValueError("conversation_draft Claim requires conversation_id")
    if namespace is MemoryNamespace.DEVICE_LOCAL and not (device_id or "").strip():
        raise ValueError("device_local Claim requires device_id")
    if namespace is MemoryNamespace.TASK_EPISODE and task_id is None:
        raise ValueError("task_episode Claim requires task_id")
    if namespace is MemoryNamespace.USER_PROFILE and any(
        value is not None for value in (project_id, conversation_id, task_id, version_id, device_id)
    ):
        raise ValueError(
            "user_profile Claim cannot bind project, conversation, task, version, or device"
        )


__all__ = [
    "CLAIM_TRANSITIONS",
    "ClaimStatus",
    "MemoryAuthority",
    "MemoryClaim",
    "MemoryClaimRevision",
    "MemoryNamespace",
    "MemoryObservation",
    "MemoryScanResult",
    "MemorySensitivity",
    "MemorySourceType",
    "MemoryTargetKind",
    "MemoryTombstone",
    "ObservationStatus",
    "memory_content_hash",
]
