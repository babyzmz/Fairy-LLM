from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.engine import Connection

from fairy_core.contracts.common import ExecutionTarget
from fairy_core.domain.ids import new_id
from fairy_core.storage.schema import (
    workflow_edges,
    workflow_instructions,
    workflow_nodes,
    workflow_plan_revisions,
    workflow_runs,
)
from fairy_core.workflow.errors import WorkflowRevisionError
from fairy_core.workflow.models import (
    WorkflowBudget,
    WorkflowBudgetTier,
    WorkflowConcurrencyPolicy,
    WorkflowEdge,
    WorkflowInstruction,
    WorkflowInstructionStatus,
    WorkflowNode,
    WorkflowNodeStatus,
    WorkflowPlanReason,
    WorkflowPlanRevision,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowSnapshot,
    WorkflowTriggerKind,
)


def conflicts(
    policy: WorkflowConcurrencyPolicy,
    keys: frozenset[str],
    active: list[tuple[WorkflowConcurrencyPolicy, frozenset[str]]],
) -> bool:
    if not active:
        return False
    if policy is WorkflowConcurrencyPolicy.SERIAL:
        return True
    return any(
        active_policy is WorkflowConcurrencyPolicy.SERIAL or bool(keys & active_keys)
        for active_policy, active_keys in active
    )


def validate_plan(
    run_id: UUID,
    revision: int,
    nodes: tuple[WorkflowNode, ...],
    edges: tuple[WorkflowEdge, ...],
) -> tuple[WorkflowNode, ...]:
    if not nodes:
        raise ValueError("Workflow plan requires at least one node")
    by_id = {node.id: node for node in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("Workflow plan node IDs must be unique")
    if any(node.run_id != run_id or node.plan_revision != revision for node in nodes):
        raise ValueError("Workflow plan nodes must match the Run and revision")
    incoming: dict[UUID, set[UUID]] = defaultdict(set)
    outgoing: dict[UUID, set[UUID]] = defaultdict(set)
    for edge in edges:
        if edge.run_id != run_id or edge.plan_revision != revision:
            raise ValueError("Workflow edge must match the Run and revision")
        if edge.from_node_id not in by_id or edge.to_node_id not in by_id:
            raise ValueError("Workflow edge references an unknown node")
        incoming[edge.to_node_id].add(edge.from_node_id)
        outgoing[edge.from_node_id].add(edge.to_node_id)
    ready = [node_id for node_id in by_id if not incoming[node_id]]
    visited: set[UUID] = set()
    queue = list(ready)
    while queue:
        node_id = queue.pop(0)
        if node_id in visited:
            continue
        visited.add(node_id)
        for target in outgoing[node_id]:
            if incoming[target] <= visited:
                queue.append(target)
    if len(visited) != len(nodes):
        raise ValueError("Workflow plan must be acyclic")
    return tuple(
        replace(
            node,
            status=(WorkflowNodeStatus.READY if node.id in ready else WorkflowNodeStatus.PENDING),
        )
        for node in nodes
    )


def run_record(tenant_id: str, run: WorkflowRun) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "id": str(run.id),
        "owner_kind": run.owner_kind,
        "owner_id": run.owner_id,
        "conversation_id": str(run.conversation_id) if run.conversation_id else None,
        "task_id": str(run.task_id) if run.task_id else None,
        "project_id": str(run.project_id) if run.project_id else None,
        "execution_target": run.execution_target.value,
        "trigger_kind": run.trigger_kind.value,
        "parent_run_id": str(run.parent_run_id) if run.parent_run_id else None,
        "status": run.status.value,
        "budget_tier": run.budget.tier.value,
        "max_model_rounds": run.budget.max_model_rounds,
        "max_tool_invocations": run.budget.max_tool_invocations,
        "max_duration_seconds": run.budget.max_duration_seconds,
        "max_parallel_nodes": run.budget.max_parallel_nodes,
        "model_rounds_used": run.model_rounds_used,
        "tool_invocations_used": run.tool_invocations_used,
        "active_plan_revision": run.active_plan_revision,
        "cancellation_revision": run.cancellation_revision,
        "engine_version": run.engine_version,
        "pause_requested": run.pause_requested,
        "idempotency_key": run.idempotency_key,
        "error_code": run.error_code,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }


def node_record(tenant_id: str, node: WorkflowNode) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "id": str(node.id),
        "run_id": str(node.run_id),
        "plan_revision": node.plan_revision,
        "node_key": node.node_key,
        "kind": node.kind,
        "payload": dict(node.payload),
        "payload_version": node.payload_version,
        "status": node.status.value,
        "concurrency_policy": node.concurrency_policy.value,
        "resource_keys": list(node.resource_keys),
        "max_attempts": node.max_attempts,
        "attempt_count": node.attempt_count,
        "available_at": node.available_at,
        "public_summary": node.public_summary,
        "result": dict(node.result) if node.result is not None else None,
        "evidence_refs": list(node.evidence_refs),
        "error_code": node.error_code,
        "created_at": node.created_at,
        "updated_at": node.updated_at,
        "started_at": node.started_at,
        "completed_at": node.completed_at,
    }


def insert_plan(
    connection: Connection,
    tenant_id: str,
    nodes: tuple[WorkflowNode, ...],
    edges: tuple[WorkflowEdge, ...],
) -> None:
    if nodes:
        connection.execute(insert(workflow_nodes), [node_record(tenant_id, node) for node in nodes])
    if edges:
        connection.execute(
            insert(workflow_edges),
            [
                {
                    "tenant_id": tenant_id,
                    "run_id": str(edge.run_id),
                    "plan_revision": edge.plan_revision,
                    "from_node_id": str(edge.from_node_id),
                    "to_node_id": str(edge.to_node_id),
                }
                for edge in edges
            ],
        )


def load_snapshot(connection: Connection, tenant_id: str, run: WorkflowRun) -> WorkflowSnapshot:
    revision_rows = (
        connection.execute(
            select(workflow_plan_revisions)
            .where(
                workflow_plan_revisions.c.tenant_id == tenant_id,
                workflow_plan_revisions.c.run_id == str(run.id),
            )
            .order_by(workflow_plan_revisions.c.revision)
        )
        .mappings()
        .all()
    )

    node_rows = (
        connection.execute(
            select(workflow_nodes)
            .where(
                workflow_nodes.c.tenant_id == tenant_id,
                workflow_nodes.c.run_id == str(run.id),
            )
            .order_by(workflow_nodes.c.plan_revision, workflow_nodes.c.created_at)
        )
        .mappings()
        .all()
    )
    edge_rows = (
        connection.execute(
            select(workflow_edges)
            .where(
                workflow_edges.c.tenant_id == tenant_id,
                workflow_edges.c.run_id == str(run.id),
            )
            .order_by(
                workflow_edges.c.plan_revision,
                workflow_edges.c.from_node_id,
                workflow_edges.c.to_node_id,
            )
        )
        .mappings()
        .all()
    )
    instruction_rows = (
        connection.execute(
            select(workflow_instructions)
            .where(
                workflow_instructions.c.tenant_id == tenant_id,
                workflow_instructions.c.run_id == str(run.id),
            )
            .order_by(workflow_instructions.c.created_at)
        )
        .mappings()
        .all()
    )
    return WorkflowSnapshot(
        run=run,
        revisions=tuple(revision_from_row(row) for row in revision_rows),
        nodes=tuple(node_from_row(row) for row in node_rows),
        edges=tuple(edge_from_row(row) for row in edge_rows),
        instructions=tuple(instruction_from_row(row) for row in instruction_rows),
    )


def persist_instruction(
    connection: Connection,
    tenant_id: str,
    run_id: UUID,
    *,
    instruction: str,
    expected_revision: int,
    idempotency_key: str,
) -> WorkflowInstruction:
    normalized_instruction = instruction.strip()
    normalized_key = idempotency_key.strip()
    if not normalized_instruction or len(normalized_instruction) > 8000:
        raise ValueError("Workflow instruction must contain 1 to 8000 characters")
    if not normalized_key or len(normalized_key) > 512:
        raise ValueError("Workflow instruction idempotency key is invalid")
    existing = (
        connection.execute(
            select(workflow_instructions).where(
                workflow_instructions.c.tenant_id == tenant_id,
                workflow_instructions.c.run_id == str(run_id),
                workflow_instructions.c.idempotency_key == normalized_key,
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        model = instruction_from_row(existing)
        if (
            model.instruction != normalized_instruction
            or model.expected_revision != expected_revision
        ):
            raise ValueError("Workflow instruction idempotency key was reused")
        return model
    statement = select(workflow_runs).where(
        workflow_runs.c.tenant_id == tenant_id,
        workflow_runs.c.id == str(run_id),
    )
    if connection.dialect.name == "postgresql":
        statement = statement.with_for_update()
    row = connection.execute(statement).mappings().one_or_none()
    if row is None:
        raise KeyError(f"Workflow Run not found: {run_id}")
    run = run_from_row(row)
    if run.status in {
        WorkflowRunStatus.COMPLETED,
        WorkflowRunStatus.CANCELLED,
        WorkflowRunStatus.FAILED,
    }:
        raise ValueError("Terminal Workflow cannot accept instructions")
    if run.active_plan_revision != expected_revision:
        raise WorkflowRevisionError("Workflow plan revision changed")
    model = WorkflowInstruction(
        id=new_id(),
        run_id=run_id,
        idempotency_key=normalized_key,
        instruction=normalized_instruction,
        expected_revision=expected_revision,
        status=WorkflowInstructionStatus.PENDING,
        applied_revision=None,
        created_at=datetime.now(UTC),
    )
    connection.execute(
        insert(workflow_instructions).values(
            tenant_id=tenant_id,
            id=str(model.id),
            run_id=str(model.run_id),
            idempotency_key=model.idempotency_key,
            instruction=model.instruction,
            expected_revision=model.expected_revision,
            status=model.status.value,
            applied_revision=None,
            created_at=model.created_at,
            applied_at=None,
        )
    )
    return model


def run_from_row(row: Mapping[str, Any]) -> WorkflowRun:
    return WorkflowRun(
        id=UUID(str(row["id"])),
        owner_kind=str(row["owner_kind"]),
        owner_id=str(row["owner_id"]),
        conversation_id=UUID(str(row["conversation_id"])) if row["conversation_id"] else None,
        task_id=UUID(str(row["task_id"])) if row["task_id"] else None,
        project_id=UUID(str(row["project_id"])) if row["project_id"] else None,
        execution_target=ExecutionTarget(row["execution_target"]),
        trigger_kind=WorkflowTriggerKind(row["trigger_kind"]),
        parent_run_id=UUID(str(row["parent_run_id"])) if row["parent_run_id"] else None,
        status=WorkflowRunStatus(row["status"]),
        budget=WorkflowBudget(
            tier=WorkflowBudgetTier(row["budget_tier"]),
            max_model_rounds=int(row["max_model_rounds"]),
            max_tool_invocations=int(row["max_tool_invocations"]),
            max_duration_seconds=int(row["max_duration_seconds"]),
            max_parallel_nodes=int(row["max_parallel_nodes"]),
        ),
        model_rounds_used=int(row["model_rounds_used"]),
        tool_invocations_used=int(row["tool_invocations_used"]),
        active_plan_revision=int(row["active_plan_revision"]),
        cancellation_revision=int(row["cancellation_revision"]),
        engine_version=int(row["engine_version"]),
        pause_requested=bool(row["pause_requested"]),
        idempotency_key=str(row["idempotency_key"]),
        error_code=str(row["error_code"]) if row["error_code"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def revision_from_row(row: Mapping[str, Any]) -> WorkflowPlanRevision:
    return WorkflowPlanRevision(
        run_id=UUID(str(row["run_id"])),
        revision=int(row["revision"]),
        reason=WorkflowPlanReason(row["reason"]),
        instruction_id=UUID(str(row["instruction_id"])) if row["instruction_id"] else None,
        created_at=row["created_at"],
    )


def node_from_row(row: Mapping[str, Any]) -> WorkflowNode:
    return WorkflowNode(
        id=UUID(str(row["id"])),
        run_id=UUID(str(row["run_id"])),
        plan_revision=int(row["plan_revision"]),
        node_key=str(row["node_key"]),
        kind=str(row["kind"]),
        payload=dict(row["payload"]),
        payload_version=int(row["payload_version"]),
        status=WorkflowNodeStatus(row["status"]),
        concurrency_policy=WorkflowConcurrencyPolicy(row["concurrency_policy"]),
        resource_keys=tuple(string_list(row["resource_keys"])),
        max_attempts=int(row["max_attempts"]),
        attempt_count=int(row["attempt_count"]),
        available_at=row["available_at"],
        public_summary=str(row["public_summary"]),
        result=dict(row["result"]) if row["result"] is not None else None,
        evidence_refs=tuple(string_list(row["evidence_refs"])),
        error_code=str(row["error_code"]) if row["error_code"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def edge_from_row(row: Mapping[str, Any]) -> WorkflowEdge:
    return WorkflowEdge(
        run_id=UUID(str(row["run_id"])),
        plan_revision=int(row["plan_revision"]),
        from_node_id=UUID(str(row["from_node_id"])),
        to_node_id=UUID(str(row["to_node_id"])),
    )


def instruction_from_row(row: Mapping[str, Any]) -> WorkflowInstruction:
    return WorkflowInstruction(
        id=UUID(str(row["id"])),
        run_id=UUID(str(row["run_id"])),
        idempotency_key=str(row["idempotency_key"]),
        instruction=str(row["instruction"]),
        expected_revision=int(row["expected_revision"]),
        status=WorkflowInstructionStatus(row["status"]),
        applied_revision=int(row["applied_revision"]) if row["applied_revision"] else None,
        created_at=row["created_at"],
        applied_at=row["applied_at"],
    )


def string_list(value: object) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("Stored Workflow string list is invalid")
    return list(value)


__all__ = [
    "conflicts",
    "edge_from_row",
    "insert_plan",
    "instruction_from_row",
    "load_snapshot",
    "node_from_row",
    "node_record",
    "persist_instruction",
    "revision_from_row",
    "run_from_row",
    "run_record",
    "string_list",
    "validate_plan",
]
