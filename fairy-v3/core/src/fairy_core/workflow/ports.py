from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from fairy_core.workflow.models import (
    WorkflowAttemptClaim,
    WorkflowBudget,
    WorkflowEdge,
    WorkflowInstruction,
    WorkflowNode,
    WorkflowPlanReason,
    WorkflowRun,
    WorkflowSnapshot,
)


class WorkflowRepository(Protocol):
    def next_wake_delay(self, *, now: datetime, maximum: float) -> float: ...

    def create(
        self,
        run: WorkflowRun,
        *,
        nodes: tuple[WorkflowNode, ...],
        edges: tuple[WorkflowEdge, ...],
    ) -> WorkflowSnapshot: ...

    def get(self, run_id: UUID) -> WorkflowSnapshot | None: ...

    def get_run(self, run_id: UUID) -> WorkflowRun | None: ...

    def get_node(self, run_id: UUID, node_id: UUID) -> WorkflowNode | None: ...

    def get_by_owner(
        self,
        *,
        owner_kind: str,
        owner_id: str,
        engine_version: int,
    ) -> WorkflowSnapshot | None: ...

    def append_plan(
        self,
        run_id: UUID,
        *,
        expected_revision: int,
        reason: WorkflowPlanReason,
        instruction_id: UUID | None,
        nodes: tuple[WorkflowNode, ...],
        edges: tuple[WorkflowEdge, ...],
    ) -> WorkflowSnapshot: ...

    def claim_ready(
        self,
        *,
        worker_id: str,
        lease_until: datetime,
        limit: int,
        blocking_parent_kinds: frozenset[str] = frozenset(),
        reserve_child_slot: bool = False,
    ) -> tuple[WorkflowAttemptClaim, ...]: ...

    def renew(self, claim: WorkflowAttemptClaim, *, lease_until: datetime) -> bool: ...

    def reserve_budget(
        self,
        run_id: UUID,
        *,
        model_rounds: int = 0,
        tool_invocations: int = 0,
    ) -> WorkflowSnapshot: ...

    def reserve_budget_run(
        self, run_id: UUID, *, model_rounds: int = 0, tool_invocations: int = 0,
    ) -> WorkflowRun: ...

    def upgrade_budget_run(self, run_id: UUID, *, budget: WorkflowBudget) -> WorkflowRun: ...

    def upgrade_budget(
        self,
        run_id: UUID,
        *,
        budget: WorkflowBudget,
    ) -> WorkflowSnapshot: ...

    def record_checkpoint(
        self, claim: WorkflowAttemptClaim, *, result: Mapping[str, Any],
        max_bytes: int = 2 * 1024 * 1024,
    ) -> bool: ...

    def complete(
        self,
        claim: WorkflowAttemptClaim,
        *,
        result: Mapping[str, Any],
        evidence_refs: tuple[str, ...],
        public_summary: str | None = None,
        next_nodes: tuple[WorkflowNode, ...] = (),
        next_edges: tuple[WorkflowEdge, ...] = (),
    ) -> WorkflowSnapshot: ...

    def retry(
        self,
        claim: WorkflowAttemptClaim,
        *,
        available_at: datetime,
        error_code: str,
    ) -> WorkflowSnapshot: ...

    def defer(
        self,
        claim: WorkflowAttemptClaim,
        *,
        available_at: datetime,
        result: Mapping[str, Any],
    ) -> WorkflowSnapshot: ...

    def wait_for_approval(
        self,
        claim: WorkflowAttemptClaim,
        *,
        result: Mapping[str, Any],
    ) -> WorkflowSnapshot: ...

    def wait_for_input(
        self,
        claim: WorkflowAttemptClaim,
        *,
        result: Mapping[str, Any],
    ) -> WorkflowSnapshot: ...

    def fail(self, claim: WorkflowAttemptClaim, *, error_code: str) -> WorkflowSnapshot: ...

    def abandon(self, claim: WorkflowAttemptClaim) -> bool: ...

    def request_pause(self, run_id: UUID) -> WorkflowSnapshot: ...

    def resume(self, run_id: UUID) -> WorkflowSnapshot: ...

    def cancel(self, run_id: UUID) -> WorkflowSnapshot: ...

    def add_instruction(
        self,
        run_id: UUID,
        *,
        instruction: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> WorkflowInstruction: ...


__all__ = ["WorkflowRepository"]
