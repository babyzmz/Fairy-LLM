from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID

from fairy_core.contracts.common import ExecutionTarget
from fairy_core.domain.ids import new_id


class WorkflowRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class WorkflowNodeStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    SUPERSEDED = "superseded"


class WorkflowAttemptStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"
    CANCELLED = "cancelled"
    WAITING = "waiting"


class WorkflowBudgetTier(StrEnum):
    NORMAL = "normal"
    DEEP = "deep"


class WorkflowTriggerKind(StrEnum):
    USER_TURN = "user_turn"
    MANUAL = "manual"
    RECOVERY = "recovery"
    DOMAIN = "domain"


class WorkflowConcurrencyPolicy(StrEnum):
    SERIAL = "serial"
    PARALLEL_READ = "parallel_read"


class WorkflowPlanReason(StrEnum):
    INITIAL = "initial"
    STEERING = "steering"
    RECOVERY = "recovery"
    REPAIR = "repair"


class WorkflowInstructionStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class WorkflowBudget:
    tier: WorkflowBudgetTier
    max_model_rounds: int
    max_tool_invocations: int
    max_duration_seconds: int
    max_parallel_nodes: int

    def __post_init__(self) -> None:
        if (
            min(
                self.max_model_rounds,
                self.max_tool_invocations,
                self.max_duration_seconds,
                self.max_parallel_nodes,
            )
            < 1
        ):
            raise ValueError("Workflow budget values must be positive")

    @classmethod
    def normal(cls) -> WorkflowBudget:
        return cls(WorkflowBudgetTier.NORMAL, 12, 32, 30 * 60, 2)

    @classmethod
    def deep(cls) -> WorkflowBudget:
        return cls(WorkflowBudgetTier.DEEP, 24, 96, 2 * 60 * 60, 4)


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    id: UUID
    owner_kind: str
    owner_id: str
    conversation_id: UUID | None
    task_id: UUID | None
    project_id: UUID | None
    execution_target: ExecutionTarget
    trigger_kind: WorkflowTriggerKind
    parent_run_id: UUID | None
    status: WorkflowRunStatus
    budget: WorkflowBudget
    model_rounds_used: int
    tool_invocations_used: int
    active_plan_revision: int
    cancellation_revision: int
    engine_version: int
    pause_requested: bool
    idempotency_key: str
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.owner_kind.strip() or not self.owner_id.strip():
            raise ValueError("Workflow owner is required")
        if not self.idempotency_key.strip():
            raise ValueError("Workflow idempotency key is required")
        if self.active_plan_revision < 1 or self.cancellation_revision < 0:
            raise ValueError("Workflow revisions are invalid")
        if not 0 <= self.model_rounds_used <= self.budget.max_model_rounds:
            raise ValueError("Workflow model-round budget usage is invalid")
        if not 0 <= self.tool_invocations_used <= self.budget.max_tool_invocations:
            raise ValueError("Workflow tool-call budget usage is invalid")
        if self.engine_version < 1:
            raise ValueError("Workflow engine version must be positive")
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")
        if self.started_at is not None:
            _aware(self.started_at, "started_at")
        if self.completed_at is not None:
            _aware(self.completed_at, "completed_at")

    @classmethod
    def create(
        cls,
        *,
        owner_kind: str,
        owner_id: str,
        execution_target: ExecutionTarget,
        trigger_kind: WorkflowTriggerKind,
        idempotency_key: str,
        budget: WorkflowBudget | None = None,
        conversation_id: UUID | None = None,
        task_id: UUID | None = None,
        project_id: UUID | None = None,
        parent_run_id: UUID | None = None,
        engine_version: int = 1,
    ) -> WorkflowRun:
        now = datetime.now(UTC)
        return cls(
            id=new_id(),
            owner_kind=owner_kind.strip(),
            owner_id=owner_id.strip(),
            conversation_id=conversation_id,
            task_id=task_id,
            project_id=project_id,
            execution_target=execution_target,
            trigger_kind=trigger_kind,
            parent_run_id=parent_run_id,
            status=WorkflowRunStatus.QUEUED,
            budget=budget or WorkflowBudget.normal(),
            model_rounds_used=0,
            tool_invocations_used=0,
            active_plan_revision=1,
            cancellation_revision=0,
            engine_version=engine_version,
            pause_requested=False,
            idempotency_key=idempotency_key.strip(),
            error_code=None,
            created_at=now,
            updated_at=now,
        )


@dataclass(frozen=True, slots=True)
class WorkflowPlanRevision:
    run_id: UUID
    revision: int
    reason: WorkflowPlanReason
    instruction_id: UUID | None
    created_at: datetime

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError("Workflow plan revision must be positive")
        _aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class WorkflowNode:
    id: UUID
    run_id: UUID
    plan_revision: int
    node_key: str
    kind: str
    payload: Mapping[str, Any]
    payload_version: int
    status: WorkflowNodeStatus
    concurrency_policy: WorkflowConcurrencyPolicy
    resource_keys: tuple[str, ...]
    max_attempts: int
    attempt_count: int
    available_at: datetime
    public_summary: str
    result: Mapping[str, Any] | None
    evidence_refs: tuple[str, ...]
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.node_key.strip() or not self.kind.strip() or not self.public_summary.strip():
            raise ValueError("Workflow node identity and summary are required")
        if self.plan_revision < 1 or self.payload_version < 1:
            raise ValueError("Workflow node versions must be positive")
        if self.max_attempts < 1 or not 0 <= self.attempt_count <= self.max_attempts:
            raise ValueError("Workflow node attempt counts are invalid")
        if len(set(self.resource_keys)) != len(self.resource_keys):
            raise ValueError("Workflow node resource keys must be unique")
        _aware(self.available_at, "available_at")
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")
        if self.started_at is not None:
            _aware(self.started_at, "started_at")
        if self.completed_at is not None:
            _aware(self.completed_at, "completed_at")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        if self.result is not None:
            object.__setattr__(self, "result", MappingProxyType(dict(self.result)))

    @classmethod
    def create(
        cls,
        *,
        run_id: UUID,
        plan_revision: int,
        node_key: str,
        kind: str,
        payload: Mapping[str, Any],
        public_summary: str,
        ready: bool = False,
        concurrency_policy: WorkflowConcurrencyPolicy = WorkflowConcurrencyPolicy.SERIAL,
        resource_keys: tuple[str, ...] = (),
        max_attempts: int = 1,
        payload_version: int = 1,
        available_at: datetime | None = None,
    ) -> WorkflowNode:
        now = datetime.now(UTC)
        return cls(
            id=new_id(),
            run_id=run_id,
            plan_revision=plan_revision,
            node_key=node_key.strip(),
            kind=kind.strip(),
            payload=payload,
            payload_version=payload_version,
            status=WorkflowNodeStatus.READY if ready else WorkflowNodeStatus.PENDING,
            concurrency_policy=concurrency_policy,
            resource_keys=tuple(sorted(resource_keys)),
            max_attempts=max_attempts,
            attempt_count=0,
            available_at=available_at or now,
            public_summary=public_summary.strip(),
            result=None,
            evidence_refs=(),
            error_code=None,
            created_at=now,
            updated_at=now,
        )


@dataclass(frozen=True, slots=True)
class WorkflowEdge:
    run_id: UUID
    plan_revision: int
    from_node_id: UUID
    to_node_id: UUID

    def __post_init__(self) -> None:
        if self.plan_revision < 1 or self.from_node_id == self.to_node_id:
            raise ValueError("Workflow edge is invalid")


@dataclass(frozen=True, slots=True)
class WorkflowAttemptClaim:
    run_id: UUID
    node_id: UUID
    attempt_number: int
    lease_owner: str
    lease_fence: int
    lease_until: datetime
    cancellation_revision: int
    plan_revision: int


@dataclass(frozen=True, slots=True)
class WorkflowInstruction:
    id: UUID
    run_id: UUID
    idempotency_key: str
    instruction: str
    expected_revision: int
    status: WorkflowInstructionStatus
    applied_revision: int | None
    created_at: datetime
    applied_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip() or not self.instruction.strip():
            raise ValueError("Workflow instruction identity and content are required")
        if self.expected_revision < 1 or (
            self.applied_revision is not None and self.applied_revision < 1
        ):
            raise ValueError("Workflow instruction revision is invalid")
        _aware(self.created_at, "created_at")
        if self.applied_at is not None:
            _aware(self.applied_at, "applied_at")


@dataclass(frozen=True, slots=True)
class WorkflowSnapshot:
    run: WorkflowRun
    revisions: tuple[WorkflowPlanRevision, ...] = field(default_factory=tuple)
    nodes: tuple[WorkflowNode, ...] = field(default_factory=tuple)
    edges: tuple[WorkflowEdge, ...] = field(default_factory=tuple)
    instructions: tuple[WorkflowInstruction, ...] = field(default_factory=tuple)


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "WorkflowAttemptClaim",
    "WorkflowAttemptStatus",
    "WorkflowBudget",
    "WorkflowBudgetTier",
    "WorkflowConcurrencyPolicy",
    "WorkflowEdge",
    "WorkflowInstruction",
    "WorkflowInstructionStatus",
    "WorkflowNode",
    "WorkflowNodeStatus",
    "WorkflowPlanReason",
    "WorkflowPlanRevision",
    "WorkflowRun",
    "WorkflowRunStatus",
    "WorkflowSnapshot",
    "WorkflowTriggerKind",
]
