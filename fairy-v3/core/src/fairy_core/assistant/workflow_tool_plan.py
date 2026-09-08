from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from itertools import pairwise
from uuid import UUID, uuid5

from fairy_core.assistant.models import AssistantTurn, ToolInvocation, ToolInvocationStatus
from fairy_core.commanding.registry import ToolConcurrency, ToolDefinition
from fairy_core.workflow.models import WorkflowConcurrencyPolicy, WorkflowEdge, WorkflowNode

ASSISTANT_STEP_TOOL_KIND = "assistant.step.tool"
ASSISTANT_STEP_JOIN_KIND = "assistant.step.join"


def assistant_tool_continuation(
    *,
    source: WorkflowNode,
    turn: AssistantTurn,
    invocations: tuple[ToolInvocation, ...],
    offered_definitions: Mapping[str, ToolDefinition],
    resource_keys_by_invocation: Mapping[UUID, tuple[str, ...]],
) -> tuple[tuple[WorkflowNode, ...], tuple[WorkflowEdge, ...]]:
    """Compile prepared calls; argument data stays in the scoped Invocation repository.

    Domain resource identities come from the trusted adapter, never model metadata.
    A serial tool without a resolved identity conservatively locks its whole executor.
    The Kernel owns actual parallelism and adds the source-to-root edges atomically.
    """
    if (
        turn.execution_engine_version != 4
        or turn.workflow_run_id != source.run_id
        or source.payload.get("turn_id") != str(turn.id)
        or turn.active_interpretation_revision is None
        or not 1 <= len(invocations) <= 96
    ):
        raise ValueError("Tool continuation does not match its interpreted Workflow Turn")
    ids = {item.id for item in invocations}
    if (
        len(ids) != len(invocations)
        or len({item.provider_call_id for item in invocations}) != len(invocations)
        or len({item.model_round for item in invocations}) != 1
        or any(a.sequence >= b.sequence for a, b in pairwise(invocations))
        or not set(resource_keys_by_invocation).issubset(ids)
    ):
        raise ValueError("Tool continuation has inconsistent call identities or ordering")
    nodes: list[WorkflowNode] = []
    edges: list[WorkflowEdge] = []
    frontier: tuple[WorkflowNode, ...] = ()
    parallel: list[WorkflowNode] = []

    def connect(parents: tuple[WorkflowNode, ...], child: WorkflowNode) -> None:
        edges.extend(
            WorkflowEdge(source.run_id, source.plan_revision, parent.id, child.id)
            for parent in parents
        )

    for invocation in invocations:
        definition = offered_definitions.get(invocation.tool_name)
        if (
            invocation.turn_id != turn.id
            or invocation.task_id != turn.task_id
            or invocation.scope_digest != turn.scope_digest
            or invocation.status not in {
                ToolInvocationStatus.CREATED, ToolInvocationStatus.REJECTED,
            }
            or definition is None
            or definition.name != invocation.tool_name
            or not definition.model_visible
        ):
            raise ValueError("Tool continuation has an invalid scope or offered definition")
        concurrent = definition.concurrency_policy is ToolConcurrency.PARALLEL_READ
        domain_keys = resource_keys_by_invocation.get(invocation.id, ())
        keys = set(definition.concurrency_resource_keys) | set(domain_keys)
        if definition.origin_id is not None:
            keys.add(f"extension:{definition.source}:{definition.origin_id}")
        elif not concurrent and not domain_keys:
            keys.add(f"executor:{definition.executor}")
        if any(not isinstance(key, str) or not key.strip() or len(key) > 255 for key in keys):
            raise ValueError("Tool continuation resource identity is invalid")
        node = _node(
            source, key=f"tool:{invocation.id}", kind=ASSISTANT_STEP_TOOL_KIND,
            payload={
                "turn_id": str(turn.id), "invocation_id": str(invocation.id),
                "definition_digest": definition.definition_digest,
                "interpretation_revision": turn.active_interpretation_revision,
                "scope_digest": turn.scope_digest,
            },
            summary="Executing a governed tool",
            policy=(WorkflowConcurrencyPolicy.PARALLEL_READ if concurrent
                    else WorkflowConcurrencyPolicy.SERIAL),
            resources=tuple(sorted(keys)),
        )
        if concurrent:
            connect(frontier, node)
            parallel.append(node)
        else:
            connect(tuple(parallel) or frontier, node)
            parallel.clear()
            frontier = (node,)
        nodes.append(node)
    join = _node(
        source, key="join", kind=ASSISTANT_STEP_JOIN_KIND,
        payload={
            "turn_id": str(turn.id),
            "invocation_ids": [str(item.id) for item in invocations],
            "model_round": invocations[0].model_round,
            "interpretation_revision": turn.active_interpretation_revision,
            "scope_digest": turn.scope_digest,
        },
        summary="Joining tool results in original call order",
    )
    connect(tuple(parallel) or frontier, join)
    return (*nodes, join), tuple(edges)


def _node(
    source: WorkflowNode, *, key: str, kind: str, payload: dict[str, object], summary: str,
    policy: WorkflowConcurrencyPolicy = WorkflowConcurrencyPolicy.SERIAL,
    resources: tuple[str, ...] = (),
) -> WorkflowNode:
    node = WorkflowNode.create(
        run_id=source.run_id, plan_revision=source.plan_revision,
        node_key=f"{source.id}:{key}", kind=kind, payload=payload,
        public_summary=summary, concurrency_policy=policy, resource_keys=resources,
        max_attempts=128,
    )
    return replace(node, id=uuid5(source.id, key))
