from __future__ import annotations

from uuid import UUID

from fairy_core.workflow.models import (
    WorkflowEdge,
    WorkflowInstructionStatus,
    WorkflowNode,
    WorkflowPlanReason,
)

ASSISTANT_WORKFLOW_ENGINE_VERSION = 2
ASSISTANT_WORKFLOW_NODE_KIND = "assistant.turn.execute"
ASSISTANT_WORKFLOW_PREPARE_NODE_KIND = "assistant.turn.prepare"


def assistant_workflow_node(
    *,
    run_id: UUID,
    revision: int,
    turn_id: UUID,
    ready: bool = False,
) -> WorkflowNode:
    return WorkflowNode.create(
        run_id=run_id,
        plan_revision=revision,
        node_key="execute",
        kind=ASSISTANT_WORKFLOW_NODE_KIND,
        payload={"turn_id": str(turn_id)},
        public_summary=(
            "Applying updated task requirements" if revision > 1 else "Preparing Fairy's response"
        ),
        ready=ready,
        resource_keys=(f"assistant-turn:{turn_id}",),
        max_attempts=128,
    )


def assistant_workflow_plan(
    *,
    run_id: UUID,
    revision: int,
    turn_id: UUID,
    ready: bool = False,
) -> tuple[tuple[WorkflowNode, ...], tuple[WorkflowEdge, ...]]:
    prepare = WorkflowNode.create(
        run_id=run_id,
        plan_revision=revision,
        node_key="prepare",
        kind=ASSISTANT_WORKFLOW_PREPARE_NODE_KIND,
        payload={"turn_id": str(turn_id)},
        public_summary=(
            "Interpreting updated task requirements"
            if revision > 1
            else "Interpreting the request"
        ),
        ready=ready,
        resource_keys=(f"assistant-turn:{turn_id}",),
        max_attempts=16,
    )
    execute = assistant_workflow_node(
        run_id=run_id,
        revision=revision,
        turn_id=turn_id,
        ready=False,
    )
    edge = WorkflowEdge(
        run_id=run_id,
        plan_revision=revision,
        from_node_id=prepare.id,
        to_node_id=execute.id,
    )
    return (prepare, execute), (edge,)


def apply_pending_assistant_steering(unit_of_work, run_id: UUID) -> bool:
    snapshot = unit_of_work.workflows.get(run_id)
    if snapshot is None:
        raise KeyError(f"Workflow Run not found: {run_id}")
    pending = tuple(
        instruction
        for instruction in snapshot.instructions
        if instruction.status is WorkflowInstructionStatus.PENDING
    )
    if not pending:
        return False
    if len(pending) != 1:
        raise ValueError("Assistant Workflow has conflicting pending instructions")
    instruction = pending[0]
    next_revision = snapshot.run.active_plan_revision + 1
    nodes, edges = assistant_workflow_plan(
        run_id=run_id,
        revision=next_revision,
        turn_id=UUID(snapshot.run.owner_id),
        ready=True,
    )
    unit_of_work.workflows.append_plan(
        run_id,
        expected_revision=snapshot.run.active_plan_revision,
        reason=WorkflowPlanReason.STEERING,
        instruction_id=instruction.id,
        nodes=nodes,
        edges=edges,
    )
    return True


__all__ = [
    "ASSISTANT_WORKFLOW_ENGINE_VERSION",
    "ASSISTANT_WORKFLOW_NODE_KIND",
    "ASSISTANT_WORKFLOW_PREPARE_NODE_KIND",
    "apply_pending_assistant_steering",
    "assistant_workflow_node",
    "assistant_workflow_plan",
]
