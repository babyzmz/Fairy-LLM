from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import ScopeContract, Task

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_MESSAGE_LENGTH = 1_000_000
_MAX_SUMMARY_LENGTH = 16_000


def _now() -> datetime:
    return datetime.now(UTC)


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM_NOTICE = "system_notice"


class MessageVisibility(StrEnum):
    USER = "user"
    DEVELOPER = "developer"
    INTERNAL = "internal"


class AssistantTurnStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_TOOL = "waiting_for_tool"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ToolInvocationStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


_TURN_TRANSITIONS: dict[AssistantTurnStatus, frozenset[AssistantTurnStatus]] = {
    AssistantTurnStatus.CREATED: frozenset(
        {
            AssistantTurnStatus.RUNNING,
            AssistantTurnStatus.CANCELLED,
            AssistantTurnStatus.FAILED,
        }
    ),
    AssistantTurnStatus.RUNNING: frozenset(
        {
            AssistantTurnStatus.WAITING_FOR_TOOL,
            AssistantTurnStatus.COMPLETED,
            AssistantTurnStatus.CANCELLED,
            AssistantTurnStatus.FAILED,
        }
    ),
    AssistantTurnStatus.WAITING_FOR_TOOL: frozenset(
        {
            AssistantTurnStatus.RUNNING,
            AssistantTurnStatus.CANCELLED,
            AssistantTurnStatus.FAILED,
        }
    ),
    AssistantTurnStatus.COMPLETED: frozenset(),
    AssistantTurnStatus.CANCELLED: frozenset(),
    AssistantTurnStatus.FAILED: frozenset(),
}

_TOOL_TRANSITIONS: dict[ToolInvocationStatus, frozenset[ToolInvocationStatus]] = {
    ToolInvocationStatus.CREATED: frozenset(
        {
            ToolInvocationStatus.QUEUED,
            ToolInvocationStatus.REJECTED,
            ToolInvocationStatus.CANCELLED,
            ToolInvocationStatus.FAILED,
        }
    ),
    ToolInvocationStatus.QUEUED: frozenset(
        {
            ToolInvocationStatus.RUNNING,
            ToolInvocationStatus.REJECTED,
            ToolInvocationStatus.CANCELLED,
            ToolInvocationStatus.FAILED,
        }
    ),
    ToolInvocationStatus.RUNNING: frozenset(
        {
            ToolInvocationStatus.COMPLETED,
            ToolInvocationStatus.CANCELLED,
            ToolInvocationStatus.FAILED,
        }
    ),
    ToolInvocationStatus.COMPLETED: frozenset(),
    ToolInvocationStatus.FAILED: frozenset(),
    ToolInvocationStatus.REJECTED: frozenset(),
    ToolInvocationStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Message:
    id: UUID
    conversation_id: UUID
    task_id: UUID
    turn_id: UUID | None
    sequence: int
    role: MessageRole
    visibility: MessageVisibility
    content: str
    created_at: datetime = field(default_factory=_now)

    @classmethod
    def create(
        cls,
        *,
        conversation_id: UUID,
        task_id: UUID,
        turn_id: UUID | None,
        sequence: int,
        role: MessageRole,
        visibility: MessageVisibility,
        content: str,
    ) -> Message:
        if isinstance(sequence, bool) or sequence < 1:
            raise ValueError("message sequence must be positive")
        normalized = content.strip()
        if not normalized:
            raise ValueError("message content is required")
        if len(normalized) > _MAX_MESSAGE_LENGTH:
            raise ValueError("message content is too large")
        return cls(
            id=new_id(),
            conversation_id=conversation_id,
            task_id=task_id,
            turn_id=turn_id,
            sequence=sequence,
            role=role,
            visibility=visibility,
            content=normalized,
        )


@dataclass(slots=True)
class AssistantTurn:
    id: UUID
    conversation_id: UUID
    task_id: UUID
    profile_id: str
    scope_digest: str
    memory_snapshot_id: UUID
    memory_snapshot_hash: str
    idempotency_key: str
    status: AssistantTurnStatus = AssistantTurnStatus.CREATED
    cancellation_revision: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    error_code: str | None = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        task: Task,
        scope: ScopeContract,
        profile_id: str,
        idempotency_key: str,
    ) -> AssistantTurn:
        if scope.task_id != task.id or scope.conversation_id != task.conversation_id:
            raise ValueError("Scope does not match the Task")
        if scope.project_id != task.project_id:
            raise ValueError("Scope does not match the Task Project")
        if task.memory_snapshot_id is None or task.memory_snapshot_hash is None:
            raise ValueError("Task must bind a Memory Snapshot before creating a Turn")
        if (
            scope.memory_snapshot_id != task.memory_snapshot_id
            or scope.memory_snapshot_hash != task.memory_snapshot_hash
        ):
            raise ValueError("Scope Memory Snapshot does not match the Task")
        normalized_profile = _required_text(profile_id, "profile_id", maximum=255)
        normalized_key = _required_text(idempotency_key, "idempotency_key", maximum=512)
        _require_digest(scope.scope_digest, "scope_digest")
        return cls(
            id=new_id(),
            conversation_id=task.conversation_id,
            task_id=task.id,
            profile_id=normalized_profile,
            scope_digest=scope.scope_digest,
            memory_snapshot_id=task.memory_snapshot_id,
            memory_snapshot_hash=task.memory_snapshot_hash,
            idempotency_key=normalized_key,
        )

    def start(self) -> None:
        self._transition_to(AssistantTurnStatus.RUNNING)
        self.started_at = self.updated_at

    def wait_for_tool(self) -> None:
        self._transition_to(AssistantTurnStatus.WAITING_FOR_TOOL)

    def resume(self) -> None:
        self._transition_to(AssistantTurnStatus.RUNNING)

    def complete(self, *, usage: dict[str, int]) -> None:
        normalized_usage: dict[str, int] = {}
        for name, value in usage.items():
            key = _required_text(name, "usage name", maximum=128)
            if isinstance(value, bool) or value < 0:
                raise ValueError("usage values must be non-negative integers")
            normalized_usage[key] = value
        self._transition_to(AssistantTurnStatus.COMPLETED)
        self.usage = normalized_usage
        self.completed_at = self.updated_at

    def cancel(self) -> None:
        self._transition_to(AssistantTurnStatus.CANCELLED)
        self.cancellation_revision += 1
        self.completed_at = self.updated_at

    def fail(self, *, error_code: str) -> None:
        normalized = _required_text(error_code, "error_code", maximum=128)
        self._transition_to(AssistantTurnStatus.FAILED)
        self.error_code = normalized
        self.completed_at = self.updated_at

    def interrupt(self) -> None:
        self.fail(error_code="WORKER_INTERRUPTED")

    def _transition_to(self, status: AssistantTurnStatus) -> None:
        if status not in _TURN_TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"cannot transition Assistant Turn from {self.status.value} to {status.value}"
            )
        self.status = status
        self.updated_at = _now()


@dataclass(slots=True)
class ToolInvocation:
    id: UUID
    turn_id: UUID
    task_id: UUID
    sequence: int
    tool_name: str
    scope_digest: str
    argument_hash: str
    arguments: dict[str, Any]
    command_run_id: UUID | None = None
    status: ToolInvocationStatus = ToolInvocationStatus.CREATED
    public_summary: str | None = None
    artifact_ids: tuple[UUID, ...] = ()
    error_code: str | None = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    @classmethod
    def create(
        cls,
        *,
        turn: AssistantTurn,
        sequence: int,
        tool_name: str,
        scope_digest: str,
        arguments: dict[str, Any],
    ) -> ToolInvocation:
        if isinstance(sequence, bool) or sequence < 1:
            raise ValueError("tool invocation sequence must be positive")
        if scope_digest != turn.scope_digest:
            raise ValueError("Scope does not match the Assistant Turn")
        _require_digest(scope_digest, "scope_digest")
        normalized_name = _required_text(tool_name, "tool_name", maximum=255)
        try:
            canonical_arguments = json.dumps(
                arguments,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            normalized_arguments = json.loads(canonical_arguments)
        except (TypeError, ValueError) as error:
            raise ValueError("tool arguments must be valid JSON") from error
        if not isinstance(normalized_arguments, dict):
            raise ValueError("tool arguments must be a JSON object")
        canonical = json.dumps(
            {"arguments": normalized_arguments, "tool_name": normalized_name},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return cls(
            id=new_id(),
            turn_id=turn.id,
            task_id=turn.task_id,
            sequence=sequence,
            tool_name=normalized_name,
            scope_digest=scope_digest,
            argument_hash=hashlib.sha256(canonical).hexdigest(),
            arguments=normalized_arguments,
        )

    def queue(self, *, command_run_id: UUID) -> None:
        self._transition_to(ToolInvocationStatus.QUEUED)
        self.command_run_id = command_run_id

    def start(self) -> None:
        if self.command_run_id is None:
            raise InvalidTransitionError("queued Tool Invocation requires a CommandRun")
        self._transition_to(ToolInvocationStatus.RUNNING)

    def complete(
        self,
        *,
        public_summary: str,
        artifact_ids: tuple[UUID, ...] = (),
    ) -> None:
        summary = _required_text(public_summary, "public_summary", maximum=_MAX_SUMMARY_LENGTH)
        self._transition_to(ToolInvocationStatus.COMPLETED)
        self.public_summary = summary
        self.artifact_ids = tuple(artifact_ids)

    def fail(self, *, error_code: str) -> None:
        normalized = _required_text(error_code, "error_code", maximum=128)
        self._transition_to(ToolInvocationStatus.FAILED)
        self.error_code = normalized

    def reject(self, *, error_code: str) -> None:
        normalized = _required_text(error_code, "error_code", maximum=128)
        self._transition_to(ToolInvocationStatus.REJECTED)
        self.error_code = normalized

    def cancel(self) -> None:
        self._transition_to(ToolInvocationStatus.CANCELLED)

    def _transition_to(self, status: ToolInvocationStatus) -> None:
        if status not in _TOOL_TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"cannot transition Tool Invocation from {self.status.value} to {status.value}"
            )
        self.status = status
        self.updated_at = _now()


def _required_text(value: str, name: str, *, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    if len(normalized) > maximum:
        raise ValueError(f"{name} is too long")
    return normalized


def _require_digest(value: str, name: str) -> None:
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


__all__ = [
    "AssistantTurn",
    "AssistantTurnStatus",
    "Message",
    "MessageRole",
    "MessageVisibility",
    "ToolInvocation",
    "ToolInvocationStatus",
]
