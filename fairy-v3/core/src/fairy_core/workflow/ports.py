from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from fairy_core.workflow.models import (
    WorkflowAttemptClaim,
    WorkflowEdge,
    WorkflowInstruction,
    WorkflowNode,
    WorkflowPlanReason,
    WorkflowRun,
    WorkflowSnapshot,
)


class WorkflowRepository(Protocol):
    def create(
        self,
        run: WorkflowRun,
        *,
        nodes: tuple[WorkflowNode, ...],
        edges: tuple[WorkflowEdge, ...],
    ) -> WorkflowSnapshot: ...

    def get(self, run_id: UUID) -> WorkflowSnapshot | None: ...

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
    ) -> tuple[WorkflowAttemptClaim, ...]: ...

    def renew(self, claim: WorkflowAttemptClaim, *, lease_until: datetime) -> bool: ...

    def reserve_budget(
        self,
        run_id: UUID,
        *,
        model_rounds: int = 0,
        tool_invocations: int = 0,
    ) -> WorkflowSnapshot: ...

    def complete(
        self,
        claim: WorkflowAttemptClaim,
        *,
        result: Mapping[str, Any],
        evidence_refs: tuple[str, ...],
        public_summary: str | None = None,
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
