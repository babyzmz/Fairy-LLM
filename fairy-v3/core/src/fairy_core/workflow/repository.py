from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Connection

from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.storage.schema import (
    workflow_attempts,
    workflow_edges,
    workflow_instructions,
    workflow_nodes,
    workflow_plan_revisions,
    workflow_runs,
)
from fairy_core.workflow.approval_repository import WorkflowApprovalRepositoryMixin
from fairy_core.workflow.budget_repository import WorkflowBudgetRepositoryMixin
from fairy_core.workflow.deadline_repository import WorkflowDeadlineRepositoryMixin
from fairy_core.workflow.errors import (
    WorkflowBudgetExceeded,
    WorkflowFenceError,
    WorkflowRevisionError,
)
from fairy_core.workflow.models import (
    WorkflowAttemptClaim,
    WorkflowAttemptStatus,
    WorkflowConcurrencyPolicy,
    WorkflowEdge,
    WorkflowInstruction,
    WorkflowInstructionStatus,
    WorkflowNode,
    WorkflowNodeStatus,
    WorkflowPlanReason,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowSnapshot,
)
from fairy_core.workflow.repository_records import (
    conflicts,
    insert_plan,
    load_snapshot,
    persist_instruction,
    run_from_row,
    run_record,
    string_list,
    validate_plan,
)

_RUN_TERMINAL = {
    WorkflowRunStatus.COMPLETED,
    WorkflowRunStatus.CANCELLED,
    WorkflowRunStatus.FAILED,
}
_NODE_TERMINAL = {
    WorkflowNodeStatus.SUCCEEDED,
    WorkflowNodeStatus.FAILED,
    WorkflowNodeStatus.CANCELLED,
    WorkflowNodeStatus.SKIPPED,
    WorkflowNodeStatus.SUPERSEDED,
}


class SqlAlchemyWorkflowRepository(
    WorkflowApprovalRepositoryMixin,
    WorkflowBudgetRepositoryMixin,
    WorkflowDeadlineRepositoryMixin,
):
    def __init__(self, connection: Connection, *, tenant_id: str) -> None:
        self._connection = connection
        self._tenant_id = normalize_tenant_id(tenant_id)

    def create(
        self,
        run: WorkflowRun,
        *,
        nodes: tuple[WorkflowNode, ...],
        edges: tuple[WorkflowEdge, ...],
    ) -> WorkflowSnapshot:
        existing = self._connection.execute(
            select(workflow_runs.c.id).where(
                workflow_runs.c.tenant_id == self._tenant_id,
                workflow_runs.c.idempotency_key == run.idempotency_key,
            )
        ).scalar_one_or_none()
        if existing is not None:
            snapshot = self.get(UUID(str(existing)))
            assert snapshot is not None
            if (
                snapshot.run.owner_kind != run.owner_kind
                or snapshot.run.owner_id != run.owner_id
                or snapshot.run.execution_target is not run.execution_target
            ):
                raise ValueError("Workflow idempotency key is bound to different work")
            return snapshot
        if run.active_plan_revision != 1 or run.status is not WorkflowRunStatus.QUEUED:
            raise ValueError("New Workflow Run must start queued at plan revision 1")
        normalized = validate_plan(run.id, 1, nodes, edges)
        self._connection.execute(insert(workflow_runs).values(run_record(self._tenant_id, run)))
        self._connection.execute(
            insert(workflow_plan_revisions).values(
                tenant_id=self._tenant_id,
                run_id=str(run.id),
                revision=1,
                reason=WorkflowPlanReason.INITIAL.value,
                instruction_id=None,
                created_at=run.created_at,
            )
        )
        insert_plan(self._connection, self._tenant_id, normalized, edges)
        snapshot = self.get(run.id)
        assert snapshot is not None
        return snapshot

    def get(self, run_id: UUID) -> WorkflowSnapshot | None:
        row = (
            self._connection.execute(
                select(workflow_runs).where(
                    workflow_runs.c.tenant_id == self._tenant_id,
                    workflow_runs.c.id == str(run_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return load_snapshot(self._connection, self._tenant_id, run_from_row(row))

    def get_by_owner(
        self,
        *,
        owner_kind: str,
        owner_id: str,
        engine_version: int,
    ) -> WorkflowSnapshot | None:
        row = (
            self._connection.execute(
                select(workflow_runs).where(
                    workflow_runs.c.tenant_id == self._tenant_id,
                    workflow_runs.c.owner_kind == owner_kind,
                    workflow_runs.c.owner_id == owner_id,
                    workflow_runs.c.engine_version == engine_version,
                )
            )
            .mappings()
            .one_or_none()
        )
        return (
            load_snapshot(self._connection, self._tenant_id, run_from_row(row))
            if row is not None
            else None
        )

    def append_plan(
        self,
        run_id: UUID,
        *,
        expected_revision: int,
        reason: WorkflowPlanReason,
        instruction_id: UUID | None,
        nodes: tuple[WorkflowNode, ...],
        edges: tuple[WorkflowEdge, ...],
    ) -> WorkflowSnapshot:
        row = self._locked_run(run_id)
        run = run_from_row(row)
        if run.status in _RUN_TERMINAL:
            raise ValueError("Terminal Workflow cannot accept another plan")
        if run.active_plan_revision != expected_revision:
            raise WorkflowRevisionError("Workflow plan revision changed")
        active_attempt = self._connection.execute(
            select(workflow_attempts.c.node_id)
            .where(
                workflow_attempts.c.tenant_id == self._tenant_id,
                workflow_attempts.c.run_id == str(run_id),
                workflow_attempts.c.status == WorkflowAttemptStatus.RUNNING.value,
            )
            .limit(1)
        ).first()
        if active_attempt is not None:
            raise WorkflowRevisionError("Workflow still has an active node attempt")
        next_revision = expected_revision + 1
        normalized = validate_plan(run_id, next_revision, nodes, edges)
        now = datetime.now(UTC)
        self._connection.execute(
            update(workflow_nodes)
            .where(
                workflow_nodes.c.tenant_id == self._tenant_id,
                workflow_nodes.c.run_id == str(run_id),
                workflow_nodes.c.plan_revision == expected_revision,
                workflow_nodes.c.status.in_(
                    (
                        WorkflowNodeStatus.PENDING.value,
                        WorkflowNodeStatus.READY.value,
                        WorkflowNodeStatus.WAITING_FOR_APPROVAL.value,
                        WorkflowNodeStatus.WAITING_FOR_INPUT.value,
                    )
                ),
            )
            .values(
                status=WorkflowNodeStatus.SUPERSEDED.value,
                updated_at=now,
                completed_at=now,
            )
        )
        self._connection.execute(
            insert(workflow_plan_revisions).values(
                tenant_id=self._tenant_id,
                run_id=str(run_id),
                revision=next_revision,
                reason=reason.value,
                instruction_id=str(instruction_id) if instruction_id is not None else None,
                created_at=now,
            )
        )
        insert_plan(self._connection, self._tenant_id, normalized, edges)
        self._connection.execute(
            update(workflow_runs)
            .where(
                workflow_runs.c.tenant_id == self._tenant_id,
                workflow_runs.c.id == str(run_id),
                workflow_runs.c.active_plan_revision == expected_revision,
            )
            .values(
                active_plan_revision=next_revision,
                status=WorkflowRunStatus.QUEUED.value,
                pause_requested=False,
                error_code=None,
                updated_at=now,
            )
        )
        if instruction_id is not None:
            self._connection.execute(
                update(workflow_instructions)
                .where(
                    workflow_instructions.c.tenant_id == self._tenant_id,
                    workflow_instructions.c.id == str(instruction_id),
                    workflow_instructions.c.run_id == str(run_id),
                    workflow_instructions.c.status == WorkflowInstructionStatus.PENDING.value,
                )
                .values(
                    status=WorkflowInstructionStatus.APPLIED.value,
                    applied_revision=next_revision,
                    applied_at=now,
                )
            )
        snapshot = self.get(run_id)
        assert snapshot is not None
        return snapshot

    def claim_ready(
        self,
        *,
        worker_id: str,
        lease_until: datetime,
        limit: int,
        blocking_parent_kinds: frozenset[str] = frozenset(),
        reserve_child_slot: bool = False,
    ) -> tuple[WorkflowAttemptClaim, ...]:
        now = datetime.now(UTC)
        if not worker_id.strip() or limit < 1 or lease_until <= now:
            raise ValueError("Workflow claim parameters are invalid")
        self._expire_overdue(now)
        self._reclaim_expired(now)
        rows = (
            self._connection.execute(
                select(workflow_nodes, workflow_runs)
                .join(
                    workflow_runs,
                    and_(
                        workflow_runs.c.tenant_id == workflow_nodes.c.tenant_id,
                        workflow_runs.c.id == workflow_nodes.c.run_id,
                        workflow_runs.c.active_plan_revision == workflow_nodes.c.plan_revision,
                    ),
                )
                .where(
                    workflow_nodes.c.tenant_id == self._tenant_id,
                    workflow_nodes.c.status == WorkflowNodeStatus.READY.value,
                    workflow_nodes.c.available_at <= now,
                    workflow_runs.c.status.in_(
                        (WorkflowRunStatus.QUEUED.value, WorkflowRunStatus.RUNNING.value)
                    ),
                    workflow_runs.c.pause_requested.is_(False),
                )
                .order_by(
                    workflow_runs.c.parent_run_id.is_not(None).desc(),
                    workflow_runs.c.updated_at,
                    workflow_nodes.c.created_at,
                    workflow_nodes.c.id,
                )
            )
            .mappings()
            .all()
        )
        active_rows = (
            self._connection.execute(
                select(
                    workflow_nodes.c.run_id,
                    workflow_nodes.c.concurrency_policy,
                    workflow_nodes.c.resource_keys,
                ).where(
                    workflow_nodes.c.tenant_id == self._tenant_id,
                    workflow_nodes.c.status == WorkflowNodeStatus.RUNNING.value,
                )
            )
            .mappings()
            .all()
        )
        active_by_run: dict[str, list[tuple[WorkflowConcurrencyPolicy, frozenset[str]]]] = (
            defaultdict(list)
        )
        for active in active_rows:
            active_by_run[str(active["run_id"])].append(
                (
                    WorkflowConcurrencyPolicy(active["concurrency_policy"]),
                    frozenset(string_list(active["resource_keys"])),
                )
            )
        selected: list[Mapping[str, Any]] = []
        selected_child = False
        selected_blocking_parent = False
        remaining = list(rows)
        while remaining and len(selected) < limit:
            selected_run_ids: set[str] = set()
            progress = False
            next_remaining: list[Mapping[str, Any]] = []
            for row in remaining:
                run_id = str(row["run_id"])
                if run_id in selected_run_ids or len(selected) >= limit:
                    next_remaining.append(row)
                    continue
                budget_limit = int(row["max_parallel_nodes"])
                active = active_by_run[run_id]
                policy = WorkflowConcurrencyPolicy(row["concurrency_policy"])
                keys = frozenset(string_list(row["resource_keys"]))
                is_child = row["parent_run_id"] is not None
                is_blocking_parent = not is_child and str(row["kind"]) in blocking_parent_kinds
                needs_child_slot = (
                    reserve_child_slot or selected_blocking_parent or is_blocking_parent
                )
                if (
                    not is_child
                    and not selected_child
                    and needs_child_slot
                    and len(selected) >= limit - 1
                ):
                    next_remaining.append(row)
                    continue
                if len(active) >= budget_limit or conflicts(policy, keys, active):
                    next_remaining.append(row)
                    continue
                selected.append(row)
                selected_run_ids.add(run_id)
                active.append((policy, keys))
                selected_child = selected_child or is_child
                selected_blocking_parent = selected_blocking_parent or is_blocking_parent
                progress = True
            if not progress:
                break
            remaining = next_remaining

        claims: list[WorkflowAttemptClaim] = []
        for row in selected:
            node_id = str(row["id"])
            attempt_number = int(row["attempt_count"]) + 1
            changed = self._connection.execute(
                update(workflow_nodes)
                .where(
                    workflow_nodes.c.tenant_id == self._tenant_id,
                    workflow_nodes.c.id == node_id,
                    workflow_nodes.c.status == WorkflowNodeStatus.READY.value,
                    workflow_nodes.c.attempt_count == int(row["attempt_count"]),
                )
                .values(
                    status=WorkflowNodeStatus.RUNNING.value,
                    attempt_count=attempt_number,
                    started_at=row["started_at"] or now,
                    updated_at=now,
                )
            ).rowcount
            if changed != 1:
                continue
            self._connection.execute(
                insert(workflow_attempts).values(
                    tenant_id=self._tenant_id,
                    node_id=node_id,
                    attempt_number=attempt_number,
                    run_id=str(row["run_id"]),
                    status=WorkflowAttemptStatus.RUNNING.value,
                    lease_owner=worker_id,
                    lease_fence=attempt_number,
                    lease_until=lease_until,
                    cancellation_revision=int(row["cancellation_revision"]),
                    plan_revision=int(row["plan_revision"]),
                    result=None,
                    evidence_refs=[],
                    error_code=None,
                    started_at=now,
                    finished_at=None,
                )
            )
            self._connection.execute(
                update(workflow_runs)
                .where(
                    workflow_runs.c.tenant_id == self._tenant_id,
                    workflow_runs.c.id == str(row["run_id"]),
                    workflow_runs.c.status.in_(
                        (WorkflowRunStatus.QUEUED.value, WorkflowRunStatus.RUNNING.value)
                    ),
                )
                .values(
                    status=WorkflowRunStatus.RUNNING.value,
                    started_at=row["started_at_1"] or now,
                    updated_at=now,
                )
            )
            claims.append(
                WorkflowAttemptClaim(
                    run_id=UUID(str(row["run_id"])),
                    node_id=UUID(node_id),
                    attempt_number=attempt_number,
                    lease_owner=worker_id,
                    lease_fence=attempt_number,
                    lease_until=lease_until,
                    cancellation_revision=int(row["cancellation_revision"]),
                    plan_revision=int(row["plan_revision"]),
                )
            )
        return tuple(claims)

    def renew(self, claim: WorkflowAttemptClaim, *, lease_until: datetime) -> bool:
        now = datetime.now(UTC)
        if lease_until <= now:
            raise ValueError("Workflow lease renewal must extend into the future")
        self._expire_overdue(now)
        changed = self._connection.execute(
            update(workflow_attempts)
            .where(*self._claim_predicates(claim, require_live=True))
            .values(lease_until=lease_until)
        ).rowcount
        return changed == 1

    def complete(
        self,
        claim: WorkflowAttemptClaim,
        *,
        result: Mapping[str, Any],
        evidence_refs: tuple[str, ...],
        public_summary: str | None = None,
    ) -> WorkflowSnapshot:
        now = datetime.now(UTC)
        self._require_claim(claim, now=now)
        self._connection.execute(
            update(workflow_attempts)
            .where(*self._claim_predicates(claim, require_live=True))
            .values(
                status=WorkflowAttemptStatus.SUCCEEDED.value,
                lease_owner=None,
                lease_until=None,
                result=dict(result),
                evidence_refs=list(evidence_refs),
                finished_at=now,
            )
        )
        values: dict[str, Any] = {
            "status": WorkflowNodeStatus.SUCCEEDED.value,
            "result": dict(result),
            "evidence_refs": list(evidence_refs),
            "error_code": None,
            "updated_at": now,
            "completed_at": now,
        }
        if public_summary is not None:
            values["public_summary"] = public_summary.strip()[:500]
        self._connection.execute(
            update(workflow_nodes)
            .where(
                workflow_nodes.c.tenant_id == self._tenant_id,
                workflow_nodes.c.id == str(claim.node_id),
                workflow_nodes.c.status == WorkflowNodeStatus.RUNNING.value,
            )
            .values(**values)
        )
        self._promote_dependents(claim.run_id, claim.plan_revision, now=now)
        self._settle_run(claim.run_id, now=now)
        snapshot = self.get(claim.run_id)
        assert snapshot is not None
        return snapshot

    def retry(
        self,
        claim: WorkflowAttemptClaim,
        *,
        available_at: datetime,
        error_code: str,
    ) -> WorkflowSnapshot:
        now = datetime.now(UTC)
        if available_at < now:
            available_at = now
        node = self._require_claim(claim, now=now)
        if int(node["attempt_count"]) >= int(node["max_attempts"]):
            return self.fail(claim, error_code=error_code)
        self._settle_attempt(
            claim,
            status=WorkflowAttemptStatus.FAILED,
            error_code=error_code,
            now=now,
        )
        self._connection.execute(
            update(workflow_nodes)
            .where(
                workflow_nodes.c.tenant_id == self._tenant_id,
                workflow_nodes.c.id == str(claim.node_id),
                workflow_nodes.c.status == WorkflowNodeStatus.RUNNING.value,
            )
            .values(
                status=WorkflowNodeStatus.READY.value,
                available_at=available_at,
                error_code=error_code[:128],
                updated_at=now,
            )
        )
        self._connection.execute(
            update(workflow_runs)
            .where(
                workflow_runs.c.tenant_id == self._tenant_id,
                workflow_runs.c.id == str(claim.run_id),
            )
            .values(status=WorkflowRunStatus.QUEUED.value, updated_at=now)
        )
        snapshot = self.get(claim.run_id)
        assert snapshot is not None
        return snapshot

    def defer(
        self,
        claim: WorkflowAttemptClaim,
        *,
        available_at: datetime,
        result: Mapping[str, Any],
    ) -> WorkflowSnapshot:
        now = datetime.now(UTC)
        if available_at <= now:
            raise ValueError("Deferred Workflow node must become available in the future")
        node = self._require_claim(claim, now=now)
        if int(node["attempt_count"]) >= int(node["max_attempts"]):
            return self.fail(claim, error_code="WORKFLOW_WAIT_BUDGET_EXHAUSTED")
        self._connection.execute(
            update(workflow_attempts)
            .where(*self._claim_predicates(claim, require_live=True))
            .values(
                status=WorkflowAttemptStatus.WAITING.value,
                lease_owner=None,
                lease_until=None,
                result=dict(result),
                finished_at=now,
            )
        )
        self._connection.execute(
            update(workflow_nodes)
            .where(
                workflow_nodes.c.tenant_id == self._tenant_id,
                workflow_nodes.c.id == str(claim.node_id),
                workflow_nodes.c.status == WorkflowNodeStatus.RUNNING.value,
            )
            .values(
                status=WorkflowNodeStatus.READY.value,
                result=dict(result),
                available_at=available_at,
                error_code=None,
                updated_at=now,
            )
        )
        self._connection.execute(
            update(workflow_runs)
            .where(
                workflow_runs.c.tenant_id == self._tenant_id,
                workflow_runs.c.id == str(claim.run_id),
            )
            .values(status=WorkflowRunStatus.QUEUED.value, updated_at=now)
        )
        snapshot = self.get(claim.run_id)
        assert snapshot is not None
        return snapshot

    def fail(self, claim: WorkflowAttemptClaim, *, error_code: str) -> WorkflowSnapshot:
        now = datetime.now(UTC)
        self._require_claim(claim, now=now)
        self._settle_attempt(
            claim,
            status=WorkflowAttemptStatus.FAILED,
            error_code=error_code,
            now=now,
        )
        self._connection.execute(
            update(workflow_nodes)
            .where(
                workflow_nodes.c.tenant_id == self._tenant_id,
                workflow_nodes.c.run_id == str(claim.run_id),
                workflow_nodes.c.status.in_(
                    (
                        WorkflowNodeStatus.PENDING.value,
                        WorkflowNodeStatus.READY.value,
                        WorkflowNodeStatus.RUNNING.value,
                        WorkflowNodeStatus.WAITING_FOR_APPROVAL.value,
                        WorkflowNodeStatus.WAITING_FOR_INPUT.value,
                    )
                ),
            )
            .values(
                status=WorkflowNodeStatus.CANCELLED.value,
                error_code=error_code[:128],
                updated_at=now,
                completed_at=now,
            )
        )
        self._connection.execute(
            update(workflow_nodes)
            .where(
                workflow_nodes.c.tenant_id == self._tenant_id,
                workflow_nodes.c.id == str(claim.node_id),
            )
            .values(status=WorkflowNodeStatus.FAILED.value)
        )
        self._cancel_running_attempts(claim.run_id, now=now, except_node_id=claim.node_id)
        self._connection.execute(
            update(workflow_runs)
            .where(
                workflow_runs.c.tenant_id == self._tenant_id,
                workflow_runs.c.id == str(claim.run_id),
            )
            .values(
                status=WorkflowRunStatus.FAILED.value,
                cancellation_revision=workflow_runs.c.cancellation_revision + 1,
                error_code=error_code[:128],
                updated_at=now,
                completed_at=now,
            )
        )
        snapshot = self.get(claim.run_id)
        assert snapshot is not None
        return snapshot

    def abandon(self, claim: WorkflowAttemptClaim) -> bool:
        now = datetime.now(UTC)
        predicates = self._claim_predicates(claim, require_live=False)
        attempt = (
            self._connection.execute(select(workflow_attempts).where(*predicates))
            .mappings()
            .one_or_none()
        )
        if attempt is None:
            return False
        run = (
            self._connection.execute(
                select(workflow_runs).where(
                    workflow_runs.c.tenant_id == self._tenant_id,
                    workflow_runs.c.id == str(claim.run_id),
                )
            )
            .mappings()
            .one()
        )
        self._connection.execute(
            update(workflow_attempts)
            .where(*predicates)
            .values(
                status=WorkflowAttemptStatus.ABANDONED.value,
                lease_owner=None,
                lease_until=None,
                error_code="WORKER_INTERRUPTED",
                finished_at=now,
            )
        )
        node = (
            self._connection.execute(
                select(workflow_nodes).where(
                    workflow_nodes.c.tenant_id == self._tenant_id,
                    workflow_nodes.c.id == str(claim.node_id),
                )
            )
            .mappings()
            .one()
        )
        if WorkflowRunStatus(run["status"]) in _RUN_TERMINAL:
            next_status = WorkflowNodeStatus.CANCELLED
        elif int(node["attempt_count"]) < int(node["max_attempts"]):
            next_status = WorkflowNodeStatus.READY
        else:
            next_status = WorkflowNodeStatus.FAILED
        self._connection.execute(
            update(workflow_nodes)
            .where(
                workflow_nodes.c.tenant_id == self._tenant_id,
                workflow_nodes.c.id == str(claim.node_id),
                workflow_nodes.c.status == WorkflowNodeStatus.RUNNING.value,
            )
            .values(
                status=next_status.value,
                error_code="WORKER_INTERRUPTED",
                updated_at=now,
                completed_at=now if next_status in _NODE_TERMINAL else None,
            )
        )
        if next_status is WorkflowNodeStatus.FAILED:
            self._connection.execute(
                update(workflow_runs)
                .where(
                    workflow_runs.c.tenant_id == self._tenant_id,
                    workflow_runs.c.id == str(claim.run_id),
                )
                .values(
                    status=WorkflowRunStatus.FAILED.value,
                    error_code="WORKER_INTERRUPTED",
                    updated_at=now,
                    completed_at=now,
                )
            )
        elif bool(run["pause_requested"]):
            self._settle_pause(claim.run_id, now=now)
        else:
            self._connection.execute(
                update(workflow_runs)
                .where(
                    workflow_runs.c.tenant_id == self._tenant_id,
                    workflow_runs.c.id == str(claim.run_id),
                    workflow_runs.c.status.not_in(tuple(status.value for status in _RUN_TERMINAL)),
                )
                .values(status=WorkflowRunStatus.QUEUED.value, updated_at=now)
            )
        return True

    def request_pause(self, run_id: UUID) -> WorkflowSnapshot:
        now = datetime.now(UTC)
        run = run_from_row(self._locked_run(run_id))
        if run.status in _RUN_TERMINAL:
            return load_snapshot(self._connection, self._tenant_id, run)
        active = self._has_running_attempt(run_id)
        self._connection.execute(
            update(workflow_runs)
            .where(
                workflow_runs.c.tenant_id == self._tenant_id,
                workflow_runs.c.id == str(run_id),
            )
            .values(
                pause_requested=True,
                status=(
                    WorkflowRunStatus.RUNNING.value if active else WorkflowRunStatus.PAUSED.value
                ),
                updated_at=now,
            )
        )
        snapshot = self.get(run_id)
        assert snapshot is not None
        return snapshot

    def resume(self, run_id: UUID) -> WorkflowSnapshot:
        now = datetime.now(UTC)
        run = run_from_row(self._locked_run(run_id))
        if run.status in _RUN_TERMINAL:
            return load_snapshot(self._connection, self._tenant_id, run)
        if run.status in {
            WorkflowRunStatus.WAITING_FOR_APPROVAL,
            WorkflowRunStatus.WAITING_FOR_INPUT,
        }:
            self._connection.execute(
                update(workflow_nodes)
                .where(
                    workflow_nodes.c.tenant_id == self._tenant_id,
                    workflow_nodes.c.run_id == str(run_id),
                    workflow_nodes.c.plan_revision == run.active_plan_revision,
                    workflow_nodes.c.status.in_(
                        (
                            WorkflowNodeStatus.WAITING_FOR_APPROVAL.value,
                            WorkflowNodeStatus.WAITING_FOR_INPUT.value,
                        )
                    ),
                )
                .values(status=WorkflowNodeStatus.READY.value, updated_at=now)
            )
        self._connection.execute(
            update(workflow_runs)
            .where(
                workflow_runs.c.tenant_id == self._tenant_id,
                workflow_runs.c.id == str(run_id),
            )
            .values(
                pause_requested=False,
                status=(
                    WorkflowRunStatus.RUNNING.value
                    if self._has_running_attempt(run_id)
                    else WorkflowRunStatus.QUEUED.value
                ),
                updated_at=now,
            )
        )
        snapshot = self.get(run_id)
        assert snapshot is not None
        return snapshot

    def cancel(self, run_id: UUID) -> WorkflowSnapshot:
        now = datetime.now(UTC)
        run = run_from_row(self._locked_run(run_id))
        if run.status in _RUN_TERMINAL:
            return load_snapshot(self._connection, self._tenant_id, run)
        self._cancel_running_attempts(run_id, now=now)
        self._connection.execute(
            update(workflow_nodes)
            .where(
                workflow_nodes.c.tenant_id == self._tenant_id,
                workflow_nodes.c.run_id == str(run_id),
                workflow_nodes.c.status.not_in(tuple(status.value for status in _NODE_TERMINAL)),
            )
            .values(
                status=WorkflowNodeStatus.CANCELLED.value,
                updated_at=now,
                completed_at=now,
            )
        )
        self._connection.execute(
            update(workflow_runs)
            .where(
                workflow_runs.c.tenant_id == self._tenant_id,
                workflow_runs.c.id == str(run_id),
            )
            .values(
                status=WorkflowRunStatus.CANCELLED.value,
                pause_requested=False,
                cancellation_revision=workflow_runs.c.cancellation_revision + 1,
                updated_at=now,
                completed_at=now,
            )
        )
        snapshot = self.get(run_id)
        assert snapshot is not None
        return snapshot

    def add_instruction(
        self,
        run_id: UUID,
        *,
        instruction: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> WorkflowInstruction:
        return persist_instruction(
            self._connection,
            self._tenant_id,
            run_id,
            instruction=instruction,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

    def _locked_run(self, run_id: UUID) -> Mapping[str, Any]:
        statement = select(workflow_runs).where(
            workflow_runs.c.tenant_id == self._tenant_id,
            workflow_runs.c.id == str(run_id),
        )
        if self._connection.dialect.name == "postgresql":
            statement = statement.with_for_update()
        row = self._connection.execute(statement).mappings().one_or_none()
        if row is None:
            raise KeyError(f"Workflow Run not found: {run_id}")
        return row

    def _claim_predicates(
        self,
        claim: WorkflowAttemptClaim,
        *,
        require_live: bool,
    ) -> tuple[Any, ...]:
        predicates: list[Any] = [
            workflow_attempts.c.tenant_id == self._tenant_id,
            workflow_attempts.c.node_id == str(claim.node_id),
            workflow_attempts.c.attempt_number == claim.attempt_number,
            workflow_attempts.c.run_id == str(claim.run_id),
            workflow_attempts.c.status == WorkflowAttemptStatus.RUNNING.value,
            workflow_attempts.c.lease_owner == claim.lease_owner,
            workflow_attempts.c.lease_fence == claim.lease_fence,
            workflow_attempts.c.cancellation_revision == claim.cancellation_revision,
            workflow_attempts.c.plan_revision == claim.plan_revision,
        ]
        if require_live:
            predicates.append(workflow_attempts.c.lease_until > datetime.now(UTC))
        return tuple(predicates)

    def _require_claim(
        self,
        claim: WorkflowAttemptClaim,
        *,
        now: datetime,
    ) -> Mapping[str, Any]:
        row = (
            self._connection.execute(
                select(
                    workflow_nodes,
                    workflow_runs.c.cancellation_revision.label("run_cancellation_revision"),
                    workflow_attempts.c.lease_until.label("attempt_lease_until"),
                )
                .join(
                    workflow_runs,
                    and_(
                        workflow_runs.c.tenant_id == workflow_nodes.c.tenant_id,
                        workflow_runs.c.id == workflow_nodes.c.run_id,
                    ),
                )
                .join(
                    workflow_attempts,
                    and_(
                        workflow_attempts.c.tenant_id == workflow_nodes.c.tenant_id,
                        workflow_attempts.c.node_id == workflow_nodes.c.id,
                    ),
                )
                .where(*self._claim_predicates(claim, require_live=False))
            )
            .mappings()
            .one_or_none()
        )
        if (
            row is None
            or row["status"] != WorkflowNodeStatus.RUNNING.value
            or row["attempt_lease_until"] is None
            or row["attempt_lease_until"] <= now
            or int(row["run_cancellation_revision"]) != claim.cancellation_revision
        ):
            raise WorkflowFenceError("Workflow attempt lease or cancellation fence was lost")
        return row

    def _settle_attempt(
        self,
        claim: WorkflowAttemptClaim,
        *,
        status: WorkflowAttemptStatus,
        error_code: str,
        now: datetime,
    ) -> None:
        self._connection.execute(
            update(workflow_attempts)
            .where(*self._claim_predicates(claim, require_live=True))
            .values(
                status=status.value,
                lease_owner=None,
                lease_until=None,
                error_code=error_code[:128],
                finished_at=now,
            )
        )

    def _reclaim_expired(self, now: datetime) -> None:
        expired = (
            self._connection.execute(
                select(workflow_attempts).where(
                    workflow_attempts.c.tenant_id == self._tenant_id,
                    workflow_attempts.c.status == WorkflowAttemptStatus.RUNNING.value,
                    workflow_attempts.c.lease_until <= now,
                )
            )
            .mappings()
            .all()
        )
        for attempt in expired:
            node = (
                self._connection.execute(
                    select(workflow_nodes).where(
                        workflow_nodes.c.tenant_id == self._tenant_id,
                        workflow_nodes.c.id == attempt["node_id"],
                    )
                )
                .mappings()
                .one()
            )
            run = (
                self._connection.execute(
                    select(workflow_runs).where(
                        workflow_runs.c.tenant_id == self._tenant_id,
                        workflow_runs.c.id == attempt["run_id"],
                    )
                )
                .mappings()
                .one()
            )
            self._connection.execute(
                update(workflow_attempts)
                .where(
                    workflow_attempts.c.tenant_id == self._tenant_id,
                    workflow_attempts.c.node_id == attempt["node_id"],
                    workflow_attempts.c.attempt_number == attempt["attempt_number"],
                    workflow_attempts.c.status == WorkflowAttemptStatus.RUNNING.value,
                    workflow_attempts.c.lease_fence == attempt["lease_fence"],
                )
                .values(
                    status=WorkflowAttemptStatus.ABANDONED.value,
                    lease_owner=None,
                    lease_until=None,
                    error_code="WORKER_LEASE_EXPIRED",
                    finished_at=now,
                )
            )
            if WorkflowRunStatus(run["status"]) in _RUN_TERMINAL:
                next_status = WorkflowNodeStatus.CANCELLED
            elif int(node["attempt_count"]) < int(node["max_attempts"]):
                next_status = WorkflowNodeStatus.READY
            else:
                next_status = WorkflowNodeStatus.FAILED
            self._connection.execute(
                update(workflow_nodes)
                .where(
                    workflow_nodes.c.tenant_id == self._tenant_id,
                    workflow_nodes.c.id == attempt["node_id"],
                    workflow_nodes.c.status == WorkflowNodeStatus.RUNNING.value,
                )
                .values(
                    status=next_status.value,
                    error_code="WORKER_LEASE_EXPIRED",
                    updated_at=now,
                    completed_at=now if next_status in _NODE_TERMINAL else None,
                )
            )
            if next_status is WorkflowNodeStatus.FAILED:
                self._connection.execute(
                    update(workflow_runs)
                    .where(
                        workflow_runs.c.tenant_id == self._tenant_id,
                        workflow_runs.c.id == attempt["run_id"],
                    )
                    .values(
                        status=WorkflowRunStatus.FAILED.value,
                        error_code="WORKER_LEASE_EXPIRED",
                        updated_at=now,
                        completed_at=now,
                    )
                )
            elif bool(run["pause_requested"]):
                self._settle_pause(UUID(str(attempt["run_id"])), now=now)
            else:
                self._connection.execute(
                    update(workflow_runs)
                    .where(
                        workflow_runs.c.tenant_id == self._tenant_id,
                        workflow_runs.c.id == attempt["run_id"],
                        workflow_runs.c.status.not_in(
                            tuple(status.value for status in _RUN_TERMINAL)
                        ),
                    )
                    .values(status=WorkflowRunStatus.QUEUED.value, updated_at=now)
                )

    def _promote_dependents(self, run_id: UUID, revision: int, *, now: datetime) -> None:
        target_ids = (
            self._connection.execute(
                select(workflow_edges.c.to_node_id).where(
                    workflow_edges.c.tenant_id == self._tenant_id,
                    workflow_edges.c.run_id == str(run_id),
                    workflow_edges.c.plan_revision == revision,
                )
            )
            .scalars()
            .all()
        )
        for target_id in set(target_ids):
            source_statuses = (
                self._connection.execute(
                    select(workflow_nodes.c.status)
                    .join(
                        workflow_edges,
                        and_(
                            workflow_edges.c.tenant_id == workflow_nodes.c.tenant_id,
                            workflow_edges.c.from_node_id == workflow_nodes.c.id,
                        ),
                    )
                    .where(
                        workflow_edges.c.tenant_id == self._tenant_id,
                        workflow_edges.c.run_id == str(run_id),
                        workflow_edges.c.plan_revision == revision,
                        workflow_edges.c.to_node_id == target_id,
                    )
                )
                .scalars()
                .all()
            )
            if source_statuses and all(
                status == WorkflowNodeStatus.SUCCEEDED.value for status in source_statuses
            ):
                self._connection.execute(
                    update(workflow_nodes)
                    .where(
                        workflow_nodes.c.tenant_id == self._tenant_id,
                        workflow_nodes.c.id == target_id,
                        workflow_nodes.c.status == WorkflowNodeStatus.PENDING.value,
                    )
                    .values(status=WorkflowNodeStatus.READY.value, updated_at=now)
                )

    def _settle_run(self, run_id: UUID, *, now: datetime) -> None:
        run = run_from_row(self._locked_run(run_id))
        active_nodes = (
            self._connection.execute(
                select(workflow_nodes.c.status).where(
                    workflow_nodes.c.tenant_id == self._tenant_id,
                    workflow_nodes.c.run_id == str(run_id),
                    workflow_nodes.c.plan_revision == run.active_plan_revision,
                )
            )
            .scalars()
            .all()
        )
        if run.pause_requested and not self._has_running_attempt(run_id):
            status = WorkflowRunStatus.PAUSED
            completed_at = None
        elif active_nodes and all(
            WorkflowNodeStatus(value) in _NODE_TERMINAL for value in active_nodes
        ):
            if any(value == WorkflowNodeStatus.FAILED.value for value in active_nodes):
                status = WorkflowRunStatus.FAILED
            elif any(value == WorkflowNodeStatus.CANCELLED.value for value in active_nodes):
                status = WorkflowRunStatus.CANCELLED
            else:
                status = WorkflowRunStatus.COMPLETED
            completed_at = now
        elif any(value == WorkflowNodeStatus.RUNNING.value for value in active_nodes):
            status = WorkflowRunStatus.RUNNING
            completed_at = None
        elif any(value == WorkflowNodeStatus.WAITING_FOR_APPROVAL.value for value in active_nodes):
            status = WorkflowRunStatus.WAITING_FOR_APPROVAL
            completed_at = None
        elif any(value == WorkflowNodeStatus.WAITING_FOR_INPUT.value for value in active_nodes):
            status = WorkflowRunStatus.WAITING_FOR_INPUT
            completed_at = None
        else:
            status = WorkflowRunStatus.QUEUED
            completed_at = None
        self._connection.execute(
            update(workflow_runs)
            .where(
                workflow_runs.c.tenant_id == self._tenant_id,
                workflow_runs.c.id == str(run_id),
            )
            .values(status=status.value, updated_at=now, completed_at=completed_at)
        )

    def _settle_pause(self, run_id: UUID, *, now: datetime) -> None:
        if not self._has_running_attempt(run_id):
            self._connection.execute(
                update(workflow_runs)
                .where(
                    workflow_runs.c.tenant_id == self._tenant_id,
                    workflow_runs.c.id == str(run_id),
                    workflow_runs.c.pause_requested.is_(True),
                    workflow_runs.c.status.not_in(tuple(status.value for status in _RUN_TERMINAL)),
                )
                .values(status=WorkflowRunStatus.PAUSED.value, updated_at=now)
            )

    def _has_running_attempt(self, run_id: UUID) -> bool:
        return (
            self._connection.execute(
                select(workflow_attempts.c.node_id)
                .where(
                    workflow_attempts.c.tenant_id == self._tenant_id,
                    workflow_attempts.c.run_id == str(run_id),
                    workflow_attempts.c.status == WorkflowAttemptStatus.RUNNING.value,
                )
                .limit(1)
            ).first()
            is not None
        )

    def _cancel_running_attempts(
        self,
        run_id: UUID,
        *,
        now: datetime,
        except_node_id: UUID | None = None,
    ) -> None:
        statement = update(workflow_attempts).where(
            workflow_attempts.c.tenant_id == self._tenant_id,
            workflow_attempts.c.run_id == str(run_id),
            workflow_attempts.c.status == WorkflowAttemptStatus.RUNNING.value,
        )
        if except_node_id is not None:
            statement = statement.where(workflow_attempts.c.node_id != str(except_node_id))
        self._connection.execute(
            statement.values(
                status=WorkflowAttemptStatus.CANCELLED.value,
                lease_owner=None,
                lease_until=None,
                error_code="WORKFLOW_CANCELLED",
                finished_at=now,
            )
        )


__all__ = ["SqlAlchemyWorkflowRepository", "WorkflowBudgetExceeded", "WorkflowFenceError",
           "WorkflowRevisionError"]
