from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.engine import Connection

from fairy_core.storage.schema import workflow_edges, workflow_nodes
from fairy_core.workflow.models import (
    WorkflowAttemptClaim,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeStatus,
)
from fairy_core.workflow.repository_records import validate_plan


def prepare_continuation(
    connection: Connection,
    tenant_id: str,
    claim: WorkflowAttemptClaim,
    nodes: tuple[WorkflowNode, ...],
    edges: tuple[WorkflowEdge, ...],
) -> tuple[tuple[WorkflowNode, ...], tuple[WorkflowEdge, ...]]:
    if not nodes:
        if edges:
            raise ValueError("Workflow continuation edges require new nodes")
        return (), ()
    if len(nodes) > 128:
        raise ValueError("Workflow continuation exceeds 128 nodes")
    if len(set(edges)) != len(edges):
        raise ValueError("Workflow continuation edges must be unique")
    if any(
        node.status not in {WorkflowNodeStatus.PENDING, WorkflowNodeStatus.READY}
        or node.attempt_count
        or node.result is not None
        or node.started_at is not None
        or node.completed_at is not None
        for node in nodes
    ):
        raise ValueError("Workflow continuation requires fresh nodes")
    normalized = validate_plan(claim.run_id, claim.plan_revision, nodes, edges)
    ids = tuple(str(node.id) for node in normalized)
    keys = {node.node_key for node in normalized}
    if len(keys) != len(nodes):
        raise ValueError("Workflow continuation node keys must be unique")
    existing_keys = tuple(
        connection.execute(
            select(workflow_nodes.c.node_key).where(
                workflow_nodes.c.tenant_id == tenant_id,
                workflow_nodes.c.run_id == str(claim.run_id),
                workflow_nodes.c.plan_revision == claim.plan_revision,
            )
        ).scalars()
    )
    if len(existing_keys) + len(nodes) > 512:
        raise ValueError("Workflow revision exceeds 512 nodes")
    if keys.intersection(existing_keys):
        raise ValueError("Workflow continuation cannot replace existing node keys")
    if (
        connection.execute(
            select(workflow_nodes.c.id)
            .where(
                workflow_nodes.c.tenant_id == tenant_id,
                workflow_nodes.c.id.in_(ids),
            )
            .limit(1)
        ).first()
        is not None
    ):
        raise ValueError("Workflow continuation cannot replace existing node IDs")
    if (
        connection.execute(
            select(workflow_edges.c.to_node_id)
            .where(
                workflow_edges.c.tenant_id == tenant_id,
                workflow_edges.c.run_id == str(claim.run_id),
                workflow_edges.c.from_node_id == str(claim.node_id),
            )
            .limit(1)
        ).first()
        is not None
    ):
        raise ValueError("Only a leaf node can append a Workflow continuation")
    roots = tuple(node for node in normalized if node.status is WorkflowNodeStatus.READY)
    parent_edges = tuple(
        WorkflowEdge(claim.run_id, claim.plan_revision, claim.node_id, node.id) for node in roots
    )
    return normalized, (*parent_edges, *edges)
