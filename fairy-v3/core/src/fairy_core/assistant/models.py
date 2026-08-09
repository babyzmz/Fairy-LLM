from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from uuid import UUID

from fairy_core.assistant.evidence import EvidenceReceipt
from fairy_core.assistant.interpretation import AssistantRequestInterpretationRevision
from fairy_core.assistant.routing import RoutingDecision
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import ScopeContract, Task
from fairy_core.model_catalog.models import (
    ModelEndpointKind,
    ModelSelectionSnapshot,
)
from fairy_core.providers.models import (
    ModelExecutionRole,
    ProviderAttemptStatus,
    ProviderErrorCategory,
)
from fairy_core.workflow.models import WorkflowBudgetTier, WorkflowRunStatus

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_MESSAGE_LENGTH = 1_000_000
_MAX_SUMMARY_LENGTH = 16_000
_MAX_TOOL_CONTENT_LENGTH = 32_000


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
    WAITING_FOR_INPUT = "waiting_for_input"
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


@dataclass(slots=True)
class ProviderAttempt:
    id: UUID
    turn_id: UUID
    task_id: UUID
    model_round: int
    attempt_number: int
    profile_id: str
    model_id: str
    endpoint_kind: ModelEndpointKind
    model_role: ModelExecutionRole
    status: ProviderAttemptStatus = ProviderAttemptStatus.STARTED
    error_category: ProviderErrorCategory | None = None
    usage: dict[str, int] = field(default_factory=dict)
    usage_cost: str | None = None
    created_at: datetime = field(default_factory=_now)
    completed_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        turn: AssistantTurn,
        model_round: int,
        attempt_number: int,
        profile_id: str,
        model_id: str,
        endpoint_kind: ModelEndpointKind,
        model_role: ModelExecutionRole,
    ) -> ProviderAttempt:
        if isinstance(model_round, bool) or model_round < 1:
            raise ValueError("provider attempt model_round must be positive")
        if isinstance(attempt_number, bool) or attempt_number < 1:
            raise ValueError("provider attempt number must be positive")
        return cls(
            id=new_id(),
            turn_id=turn.id,
            task_id=turn.task_id,
            model_round=model_round,
            attempt_number=attempt_number,
            profile_id=_required_text(profile_id, "profile_id", maximum=255),
            model_id=_required_text(model_id, "model_id", maximum=255),
            endpoint_kind=ModelEndpointKind(endpoint_kind),
            model_role=ModelExecutionRole(model_role),
        )

    def succeed(self, usage: dict[str, int], *, usage_cost: str | None = None) -> None:
        if self.status is not ProviderAttemptStatus.STARTED:
            raise InvalidTransitionError("provider attempt is already terminal")
        self.status = ProviderAttemptStatus.SUCCEEDED
        self.usage = _normalized_usage(usage)
        self.usage_cost = _normalized_cost(usage_cost)
        self.completed_at = _now()

    def fail(
        self,
        *,
        error_category: ProviderErrorCategory,
        usage: dict[str, int],
        usage_cost: str | None = None,
    ) -> None:
        if self.status is not ProviderAttemptStatus.STARTED:
            raise InvalidTransitionError("provider attempt is already terminal")
        self.status = ProviderAttemptStatus.FAILED
        self.error_category = ProviderErrorCategory(error_category)
        self.usage = _normalized_usage(usage)
        self.usage_cost = _normalized_cost(usage_cost)
        self.completed_at = _now()


_TURN_TRANSITIONS: dict[AssistantTurnStatus, frozenset[AssistantTurnStatus]] = {
    AssistantTurnStatus.CREATED: frozenset(
        {
            AssistantTurnStatus.RUNNING,
            AssistantTurnStatus.WAITING_FOR_INPUT,
            AssistantTurnStatus.CANCELLED,
            AssistantTurnStatus.FAILED,
        }
    ),
    AssistantTurnStatus.RUNNING: frozenset(
        {
            AssistantTurnStatus.WAITING_FOR_TOOL,
            AssistantTurnStatus.WAITING_FOR_INPUT,
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
    AssistantTurnStatus.WAITING_FOR_INPUT: frozenset(
        {
            AssistantTurnStatus.CREATED,
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


@dataclass(frozen=True, slots=True)
class ImportedMessage:
    id: UUID
    conversation_id: UUID
    task_id: UUID
    turn_id: UUID | None
    sequence: int
    role: MessageRole
    visibility: MessageVisibility
    content: str
    created_at: datetime
    source_conversation_id: UUID
    source_message_id: UUID
    source_hash: str
    imported_at: datetime = field(default_factory=_now)

    @classmethod
    def from_message(
        cls,
        *,
        destination_conversation_id: UUID,
        sequence: int,
        source: Message,
    ) -> ImportedMessage:
        payload = {
            "content": source.content,
            "conversation_id": str(source.conversation_id),
            "created_at": source.created_at.isoformat(),
            "id": str(source.id),
            "role": source.role.value,
            "sequence": source.sequence,
            "task_id": str(source.task_id),
            "turn_id": str(source.turn_id) if source.turn_id else None,
            "visibility": source.visibility.value,
        }
        source_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return cls(
            id=new_id(),
            conversation_id=destination_conversation_id,
            task_id=source.task_id,
            turn_id=source.turn_id,
            sequence=sequence,
            role=source.role,
            visibility=source.visibility,
            content=source.content,
            created_at=source.created_at,
            source_conversation_id=source.conversation_id,
            source_message_id=source.id,
            source_hash=source_hash,
        )


@dataclass(frozen=True, slots=True)
class ConversationMove:
    idempotency_key: str
    source_conversation_id: UUID
    destination_conversation_id: UUID
    target_project_id: UUID
    imported_count: int
    created_at: datetime = field(default_factory=_now)


@dataclass(frozen=True, slots=True)
class AssistantWorkflowSummary:
    run_id: UUID
    status: WorkflowRunStatus
    budget_tier: WorkflowBudgetTier
    active_plan_revision: int
    current_phase: str | None
    public_summary: str | None
    completed_nodes: int
    total_nodes: int
    model_rounds_used: int
    max_model_rounds: int
    tool_invocations_used: int
    max_tool_invocations: int
    pause_requested: bool
    updated_at: datetime


@dataclass(slots=True)
class AssistantTurn:
    id: UUID
    conversation_id: UUID
    task_id: UUID
    profile_id: str
    scope_digest: str
    memory_snapshot_id: UUID
    memory_snapshot_hash: str
    knowledge_snapshot_id: UUID | None
    knowledge_snapshot_hash: str | None
    harness_manifest_id: UUID | None
    harness_manifest_hash: str | None
    idempotency_key: str
    workflow_run_id: UUID | None = None
    execution_engine_version: int = 1
    active_interpretation_revision: int | None = None
    interpretation_summary: AssistantRequestInterpretationRevision | None = None
    workflow_summary: AssistantWorkflowSummary | None = None
    model_selection: ModelSelectionSnapshot | None = None
    routing_decision: RoutingDecision | None = None
    budget_approval_run_id: UUID | None = None
    cited_evidence_receipt_ids: tuple[UUID, ...] = ()
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
        model_selection: ModelSelectionSnapshot | None = None,
        execution_engine_version: int = 1,
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
        if task.knowledge_snapshot_id is None or task.knowledge_snapshot_hash is None:
            raise ValueError("Task must bind a Knowledge Snapshot before creating a Turn")
        if (
            scope.knowledge_snapshot_id != task.knowledge_snapshot_id
            or scope.knowledge_snapshot_hash != task.knowledge_snapshot_hash
        ):
            raise ValueError("Scope Knowledge Snapshot does not match the Task")
        if task.harness_manifest_id is None or task.harness_manifest_hash is None:
            raise ValueError("Task must bind a Harness Manifest before creating a Turn")
        normalized_profile = _required_text(profile_id, "profile_id", maximum=255)
        normalized_key = _required_text(idempotency_key, "idempotency_key", maximum=512)
        _require_digest(scope.scope_digest, "scope_digest")
        if execution_engine_version < 1:
            raise ValueError("Assistant execution engine version must be positive")
        return cls(
            id=new_id(),
            conversation_id=task.conversation_id,
            task_id=task.id,
            profile_id=normalized_profile,
            scope_digest=scope.scope_digest,
            memory_snapshot_id=task.memory_snapshot_id,
            memory_snapshot_hash=task.memory_snapshot_hash,
            knowledge_snapshot_id=task.knowledge_snapshot_id,
            knowledge_snapshot_hash=task.knowledge_snapshot_hash,
            harness_manifest_id=task.harness_manifest_id,
            harness_manifest_hash=task.harness_manifest_hash,
            idempotency_key=normalized_key,
            execution_engine_version=execution_engine_version,
            model_selection=model_selection,
        )

    def bind_workflow(self, run_id: UUID, *, engine_version: int) -> None:
        if engine_version < 2:
            raise ValueError("Workflow-backed Assistant engine version must be at least 2")
        if self.workflow_run_id not in {None, run_id}:
            raise InvalidTransitionError("Assistant Turn is already bound to another Workflow")
        if self.execution_engine_version not in {engine_version, 1}:
            raise InvalidTransitionError("Assistant Turn is already bound to another engine")
        if self.is_terminal:
            raise InvalidTransitionError("terminal Assistant Turn cannot bind a Workflow")
        self.workflow_run_id = run_id
        self.execution_engine_version = engine_version
        self.updated_at = _now()

    def bind_routing(self, decision: RoutingDecision) -> None:
        if self.routing_decision is not None:
            if self.routing_decision != decision:
                raise InvalidTransitionError("Assistant Turn routing is already bound")
            return
        if self.is_terminal:
            raise InvalidTransitionError("terminal Assistant Turn cannot bind routing")
        self.routing_decision = decision
        self.updated_at = _now()

    def bind_interpretation(self, revision: int, *, expected_revision: int | None) -> None:
        if revision < 1:
            raise ValueError("interpretation revision must be positive")
        if self.active_interpretation_revision != expected_revision:
            raise InvalidTransitionError("Assistant Turn interpretation changed concurrently")
        if expected_revision is not None and revision != expected_revision + 1:
            raise InvalidTransitionError(
                "Assistant Turn interpretation revision must be sequential"
            )
        if expected_revision is None and revision != 1:
            raise InvalidTransitionError("Assistant Turn must start at interpretation revision 1")
        if self.is_terminal:
            raise InvalidTransitionError("terminal Assistant Turn cannot bind interpretation")
        self.active_interpretation_revision = revision
        self.updated_at = _now()

    def bind_routing_evidence(self, decision: RoutingDecision) -> None:
        current = self.routing_decision
        if current is None or current.evidence_classified or not decision.evidence_classified:
            raise InvalidTransitionError("Assistant Turn is not waiting for evidence routing")
        expected = replace(
            current,
            requires_workspace_changes=decision.requires_workspace_changes,
            public_summary=decision.public_summary,
            evidence_requirements=decision.evidence_requirements,
            evidence_classified=True,
        )
        if expected != decision:
            raise InvalidTransitionError("evidence classification changed the selected route")
        if self.is_terminal:
            raise InvalidTransitionError("terminal Assistant Turn cannot bind routing evidence")
        self.routing_decision = decision
        self.updated_at = _now()

    def wait_for_budget_approval(self, *, command_run_id: UUID) -> None:
        if self.routing_decision is None or not self.routing_decision.approval_required:
            raise InvalidTransitionError("Assistant Turn does not require budget approval")
        if self.budget_approval_run_id not in {None, command_run_id}:
            raise InvalidTransitionError("Assistant Turn budget approval is already bound")
        self.budget_approval_run_id = command_run_id
        self.wait_for_tool()

    def resume_budget_approval(self) -> None:
        if self.budget_approval_run_id is None:
            raise InvalidTransitionError("Assistant Turn has no budget approval")
        self.resume()

    def start(self) -> None:
        self._transition_to(AssistantTurnStatus.RUNNING)
        self.started_at = self.updated_at

    def wait_for_tool(self) -> None:
        self._transition_to(AssistantTurnStatus.WAITING_FOR_TOOL)

    def wait_for_input(self) -> None:
        self._transition_to(AssistantTurnStatus.WAITING_FOR_INPUT)

    def resume_from_input(self) -> None:
        self._transition_to(
            AssistantTurnStatus.RUNNING
            if self.started_at is not None
            else AssistantTurnStatus.CREATED
        )

    def reopen_routing_after_input(self) -> None:
        if self.status is not AssistantTurnStatus.WAITING_FOR_INPUT:
            raise InvalidTransitionError("Assistant Turn is not waiting for user input")
        if self.budget_approval_run_id is not None:
            raise InvalidTransitionError(
                "Assistant Turn cannot reinterpret after budget approval started"
            )
        self.routing_decision = None
        self.updated_at = _now()

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

    def cite_evidence(self, receipt_ids: tuple[UUID, ...]) -> None:
        if self.is_terminal:
            raise InvalidTransitionError("terminal Assistant Turn cannot cite evidence")
        normalized = tuple(receipt_ids)
        if len(normalized) != len(set(normalized)):
            raise ValueError("cited evidence receipt ids must be unique")
        self.cited_evidence_receipt_ids = normalized
        self.updated_at = _now()

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

    @property
    def is_terminal(self) -> bool:
        return not _TURN_TRANSITIONS[self.status]

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
    model_round: int
    sequence: int
    provider_call_id: str
    tool_name: str
    scope_digest: str
    argument_hash: str
    arguments: dict[str, Any]
    command_run_id: UUID | None = None
    status: ToolInvocationStatus = ToolInvocationStatus.CREATED
    public_summary: str | None = None
    model_content: str | None = None
    artifact_ids: tuple[UUID, ...] = ()
    evidence_receipts: tuple[EvidenceReceipt, ...] = ()
    error_code: str | None = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    @classmethod
    def create(
        cls,
        *,
        turn: AssistantTurn,
        model_round: int,
        sequence: int,
        provider_call_id: str,
        tool_name: str,
        scope_digest: str,
        arguments: dict[str, Any],
    ) -> ToolInvocation:
        if isinstance(model_round, bool) or model_round < 1:
            raise ValueError("tool invocation model_round must be positive")
        if isinstance(sequence, bool) or sequence < 1:
            raise ValueError("tool invocation sequence must be positive")
        if scope_digest != turn.scope_digest:
            raise ValueError("Scope does not match the Assistant Turn")
        _require_digest(scope_digest, "scope_digest")
        normalized_name = _required_text(tool_name, "tool_name", maximum=255)
        normalized_call_id = _required_text(
            provider_call_id,
            "provider_call_id",
            maximum=255,
        )
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
            model_round=model_round,
            sequence=sequence,
            provider_call_id=normalized_call_id,
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
        model_content: str,
        artifact_ids: tuple[UUID, ...] = (),
        evidence_receipts: tuple[EvidenceReceipt, ...] = (),
    ) -> None:
        summary = _required_text(public_summary, "public_summary", maximum=_MAX_SUMMARY_LENGTH)
        content = _required_text(
            model_content,
            "model_content",
            maximum=_MAX_TOOL_CONTENT_LENGTH,
        )
        self._transition_to(ToolInvocationStatus.COMPLETED)
        self.public_summary = summary
        self.model_content = content
        self.artifact_ids = tuple(artifact_ids)
        receipts = tuple(evidence_receipts)
        if len({receipt.id for receipt in receipts}) != len(receipts):
            raise ValueError("Tool Invocation evidence receipt ids must be unique")
        if any(
            receipt.tool_invocation_id != self.id
            or receipt.turn_id != self.turn_id
            or receipt.task_id != self.task_id
            or receipt.scope_digest != self.scope_digest
            or receipt.tool_name != self.tool_name
            for receipt in receipts
        ):
            raise ValueError("Evidence Receipt does not match its Tool Invocation")
        self.evidence_receipts = receipts

    def revise_completed_result(
        self,
        *,
        public_summary: str,
        model_content: str,
    ) -> None:
        """Replace a provisional result after its external approval is decided."""
        if self.status is not ToolInvocationStatus.COMPLETED:
            raise InvalidTransitionError("only a completed Tool Invocation can revise its result")
        self.public_summary = _required_text(
            public_summary,
            "public_summary",
            maximum=_MAX_SUMMARY_LENGTH,
        )
        self.model_content = _required_text(
            model_content,
            "model_content",
            maximum=_MAX_TOOL_CONTENT_LENGTH,
        )
        self.updated_at = _now()

    def fail(self, *, error_code: str) -> None:
        normalized = _required_text(error_code, "error_code", maximum=128)
        self._transition_to(ToolInvocationStatus.FAILED)
        self.error_code = normalized

    def reject(self, *, error_code: str, model_content: str | None = None) -> None:
        normalized = _required_text(error_code, "error_code", maximum=128)
        content = model_content or f"Tool execution was rejected ({normalized})."
        self._transition_to(ToolInvocationStatus.REJECTED)
        self.error_code = normalized
        self.model_content = _required_text(
            content,
            "model_content",
            maximum=_MAX_TOOL_CONTENT_LENGTH,
        )

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


def _normalized_usage(usage: dict[str, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for name, value in usage.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("provider attempt usage must contain non-negative integers")
        result[_required_text(name, "usage name", maximum=128)] = value
    return result


def _normalized_cost(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("provider attempt usage cost is invalid") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError("provider attempt usage cost must be finite and non-negative")
    return format(amount, "f").rstrip("0").rstrip(".") or "0"


__all__ = [
    "AssistantTurn",
    "AssistantTurnStatus",
    "AssistantWorkflowSummary",
    "Message",
    "MessageRole",
    "MessageVisibility",
    "ProviderAttempt",
    "ToolInvocation",
    "ToolInvocationStatus",
]
