from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from fairy_core.assistant.candidates import arguments_for_definition
from fairy_core.assistant.models import ToolInvocation
from fairy_core.assistant.tool_revision import active_tool_objective, active_tool_revision
from fairy_core.assistant.tools import DuplicateToolCandidateError, ToolCandidateError
from fairy_core.assistant.turn_reader import require_task, require_turn
from fairy_core.assistant.workflow_tool_plan import (
    ASSISTANT_STEP_JOIN_KIND,
    ASSISTANT_STEP_TOOL_KIND,
    assistant_tool_continuation,
)
from fairy_core.workflow.models import WorkflowConcurrencyPolicy, WorkflowEdge, WorkflowNode


def prepare_tool_checkpoint(boundary, application, payload):
    """Persist calls and their frozen dependency/policy graph with model completion."""
    definitions = payload["offered_definitions"]
    candidates = payload["candidates"]
    parsed = []
    for candidate in candidates:
        definition = definitions.get(candidate.name)
        if definition is None:
            raise ToolCandidateError("The model requested a tool that was not offered")
        parsed.append((candidate, arguments_for_definition(candidate, definition)))
    with boundary.factory() as unit:
        turn = require_turn(unit, payload["turn_id"])
        task = require_task(unit, turn.task_id)
        scope = application._scope_resolver(unit.state, task)
        run_id, revision = active_tool_revision(unit, turn)
        objective = active_tool_objective(unit, turn)
        if (run_id, revision) != (boundary.node.run_id, boundary.node.plan_revision):
            raise ValueError("Tool checkpoint does not own the current Workflow revision")
        if objective != boundary.node.payload.get("objective_index", 0):
            raise ValueError("Tool checkpoint does not own the current objective")
        existing = unit.assistant.list_tool_invocations(turn.id)
        sequence = max((item.sequence for item in existing), default=0)
        invocations = tuple(
            ToolInvocation.create(
                turn=turn,
                model_round=payload["model_round"],
                sequence=sequence + index,
                provider_call_id=candidate.call_id,
                tool_name=candidate.name,
                scope_digest=scope.scope_digest,
                arguments=arguments,
                workflow_run_id=run_id,
                workflow_plan_revision=revision,
                workflow_objective_index=objective,
            )
            for index, (candidate, arguments) in enumerate(parsed, 1)
        )
        resources = {}
        for invocation in invocations:
            if invocation.tool_name.startswith("browser."):
                # Browser operations share mutable sessions, even snapshot reads.
                resources[invocation.id] = (f"browser-workspace:{scope.workspace_id}",)
            if any(
                item.workflow_plan_revision == invocation.workflow_plan_revision
                and item.workflow_objective_index == invocation.workflow_objective_index
                and (item.argument_hash == invocation.argument_hash
                     or item.provider_call_id == invocation.provider_call_id)
                for item in existing
            ):
                raise DuplicateToolCandidateError("Repeated tool call in this plan was rejected")
        nodes, edges = assistant_tool_continuation(
            source=boundary.node,
            turn=turn,
            invocations=invocations,
            offered_definitions=definitions,
            resource_keys_by_invocation=resources,
        )
        continuation_state = {
            "usage": dict(payload["usage"]),
            "chunk_index": payload["chunk_index"],
            "feedback": list(boundary.feedback),
            "verification_issues": list(boundary.node.payload.get("verification_issues", ())),
            "invalid_tool_retry_used": boundary.invalid_tool_retry_used,
        }
        nodes = tuple(
            replace(node, payload={**node.payload, **continuation_state})
            if node.kind == ASSISTANT_STEP_JOIN_KIND
            else node
            for node in nodes
        )
        checkpoint = {
            "type": "tools",
            "turn_id": str(turn.id),
            "model_command_id": str(payload["run"].id),
            "nodes": [_node_record(node) for node in nodes],
            "edges": [[str(edge.from_node_id), str(edge.to_node_id)] for edge in edges],
        }
        unit.workflows.record_checkpoint(boundary.claim, result=checkpoint)
        for invocation in invocations:
            unit.assistant.save_tool_invocation(invocation)
        application._wait_for_tools_in_unit(
            unit,
            turn_id=turn.id,
            run=payload["run"],
            candidate_count=len(invocations),
        )
        unit.commit()
    return checkpoint


def restore_tool_checkpoint(source, checkpoint):
    nodes = []
    for record in checkpoint["nodes"]:
        if record["kind"] not in {ASSISTANT_STEP_TOOL_KIND, ASSISTANT_STEP_JOIN_KIND}:
            raise ValueError("Unexpected kind in tool continuation checkpoint")
        node = WorkflowNode.create(
            run_id=source.run_id,
            plan_revision=source.plan_revision,
            node_key=record["node_key"],
            kind=record["kind"],
            payload=record["payload"],
            concurrency_policy=WorkflowConcurrencyPolicy(record["concurrency_policy"]),
            resource_keys=tuple(record["resource_keys"]),
            max_attempts=record["max_attempts"],
            public_summary=record["public_summary"],
        )
        nodes.append(replace(node, id=UUID(record["id"])))
    edges = tuple(
        WorkflowEdge(source.run_id, source.plan_revision, UUID(parent), UUID(child))
        for parent, child in checkpoint["edges"]
    )
    return tuple(nodes), edges


def _node_record(node):
    return {
        "id": str(node.id),
        "node_key": node.node_key,
        "kind": node.kind,
        "payload": dict(node.payload),
        "concurrency_policy": node.concurrency_policy.value,
        "resource_keys": list(node.resource_keys),
        "max_attempts": node.max_attempts,
        "public_summary": node.public_summary,
    }
