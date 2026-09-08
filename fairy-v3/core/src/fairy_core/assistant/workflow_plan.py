from __future__ import annotations

from itertools import pairwise
from uuid import UUID

from fairy_core.assistant.models import AssistantTurnStatus, ToolInvocationStatus
from fairy_core.assistant.workflow_step_nodes import STEP_ROUTE
from fairy_core.assistant.workflow_tool_plan import assistant_tool_continuation
from fairy_core.workflow.models import (
    WorkflowEdge,
    WorkflowInstructionStatus,
    WorkflowNode,
    WorkflowPlanReason,
)

ASSISTANT_WORKFLOW_ENGINE_VERSION = 3
ASSISTANT_LEGACY_WORKFLOW_ENGINE_VERSION = 2
ASSISTANT_WORKFLOW_NODE_KIND = "assistant.turn.execute"
ASSISTANT_WORKFLOW_PREPARE_NODE_KIND = "assistant.turn.prepare"
ASSISTANT_REQUEST_INTERPRET_NODE_KIND = "assistant.request.interpret"
ASSISTANT_ROUTE_SELECT_NODE_KIND = "assistant.route.select"
ASSISTANT_MODEL_ROUND_NODE_KIND = "assistant.model.round"
ASSISTANT_TOOL_INVOKE_NODE_KIND = "assistant.tool.invoke"
ASSISTANT_TOOL_JOIN_NODE_KIND = "assistant.tool.join"
ASSISTANT_RESPONSE_VERIFY_NODE_KIND = "assistant.response.verify"
ASSISTANT_RESPONSE_FINALIZE_NODE_KIND = "assistant.response.finalize"
ASSISTANT_CONTINUATION_NODE_KINDS = (
    ASSISTANT_REQUEST_INTERPRET_NODE_KIND,
    ASSISTANT_ROUTE_SELECT_NODE_KIND,
    ASSISTANT_MODEL_ROUND_NODE_KIND,
    ASSISTANT_TOOL_INVOKE_NODE_KIND,
    ASSISTANT_TOOL_JOIN_NODE_KIND,
    ASSISTANT_RESPONSE_VERIFY_NODE_KIND,
    ASSISTANT_RESPONSE_FINALIZE_NODE_KIND,
)


def assistant_workflow_node(
    *,
    run_id: UUID,
    revision: int,
    turn_id: UUID,
    ready: bool = False,
) -> WorkflowNode:
    """Build the engine-v2 execution node for already-bound legacy runs."""

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
    summaries = (
        "Interpreting updated task requirements"
        if revision > 1
        else "Interpreting the request",
        "Selecting the governed route",
        "Running the model continuation",
        "Reconciling durable tool invocations",
        "Joining tool results in call order",
        "Verifying the response contract",
        "Finalizing the unique response",
    )
    nodes = tuple(
        WorkflowNode.create(
            run_id=run_id,
            plan_revision=revision,
            node_key=kind.removeprefix("assistant."),
            kind=kind,
            payload={"turn_id": str(turn_id)},
            public_summary=summary,
            ready=ready and index == 0,
            resource_keys=(f"assistant-turn:{turn_id}",),
            max_attempts=128 if kind == ASSISTANT_MODEL_ROUND_NODE_KIND else 16,
        )
        for index, (kind, summary) in enumerate(
            zip(ASSISTANT_CONTINUATION_NODE_KINDS, summaries, strict=True)
        )
    )
    edges = tuple(
        WorkflowEdge(
            run_id=run_id,
            plan_revision=revision,
            from_node_id=source.id,
            to_node_id=target.id,
        )
        for source, target in pairwise(nodes)
    )
    return nodes, edges


def legacy_assistant_workflow_plan(
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


def assistant_step_workflow_plan(
    *, run_id: UUID, revision: int, turn_id: UUID, ready: bool = False,
) -> tuple[tuple[WorkflowNode, ...], tuple[WorkflowEdge, ...]]:
    nodes = tuple(
        WorkflowNode.create(
            run_id=run_id, plan_revision=revision, node_key=kind, kind=kind,
            payload={"turn_id": str(turn_id)}, public_summary=summary,
            ready=ready and index == 0, max_attempts=16,
            resource_keys=(f"assistant-turn:{turn_id}",),
        )
        for index, (kind, summary) in enumerate((
            (ASSISTANT_REQUEST_INTERPRET_NODE_KIND, "Interpreting the request"),
            (STEP_ROUTE, "Selecting the governed route"),
        ))
    )
    return nodes, (WorkflowEdge(run_id, revision, nodes[0].id, nodes[1].id),)


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
    if snapshot.run.engine_version == 4:
        planner = assistant_step_workflow_plan
    elif snapshot.run.engine_version >= ASSISTANT_WORKFLOW_ENGINE_VERSION:
        planner = assistant_workflow_plan
    else:
        planner = legacy_assistant_workflow_plan
    nodes, edges = planner(
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
    turn = unit_of_work.assistant.get_turn(UUID(snapshot.run.owner_id))
    if (
        turn is not None and turn.status is AssistantTurnStatus.WAITING_FOR_TOOL
        and turn.budget_approval_run_id is None
    ):
        invocations = unit_of_work.assistant.list_tool_invocations(turn.id)
        if invocations and all(invocation.status in {
            ToolInvocationStatus.COMPLETED, ToolInvocationStatus.FAILED,
            ToolInvocationStatus.REJECTED, ToolInvocationStatus.CANCELLED,
        } for invocation in invocations):
            # The superseded fan-in would have resumed this state. Preserve the
            # finished facts, without mistaking them for a new approval wait.
            expected_status = turn.status
            turn.resume()
            unit_of_work.assistant.update_turn(
                turn, expected_status=expected_status,
                expected_cancellation_revision=turn.cancellation_revision,
            )
    return True


__all__ = [
    "ASSISTANT_CONTINUATION_NODE_KINDS",
    "ASSISTANT_LEGACY_WORKFLOW_ENGINE_VERSION",
    "ASSISTANT_MODEL_ROUND_NODE_KIND",
    "ASSISTANT_REQUEST_INTERPRET_NODE_KIND",
    "ASSISTANT_RESPONSE_FINALIZE_NODE_KIND",
    "ASSISTANT_RESPONSE_VERIFY_NODE_KIND",
    "ASSISTANT_ROUTE_SELECT_NODE_KIND",
    "ASSISTANT_TOOL_INVOKE_NODE_KIND",
    "ASSISTANT_TOOL_JOIN_NODE_KIND",
    "ASSISTANT_WORKFLOW_ENGINE_VERSION",
    "ASSISTANT_WORKFLOW_NODE_KIND",
    "ASSISTANT_WORKFLOW_PREPARE_NODE_KIND",
    "apply_pending_assistant_steering",
    "assistant_step_workflow_plan",
    "assistant_tool_continuation",
    "assistant_workflow_node",
    "assistant_workflow_plan",
    "legacy_assistant_workflow_plan",
]
