from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fairy_core.assistant.candidates import ToolCandidate
from fairy_core.assistant.models import AssistantTurnStatus, ToolInvocationStatus
from fairy_core.assistant.workflow_step_nodes import STEP_MODEL, step_node
from fairy_core.assistant.workflow_tool_plan import ASSISTANT_STEP_TOOL_KIND
from fairy_core.commanding import CommandStatus
from fairy_core.workflow.errors import WorkflowFenceError
from fairy_core.workflow.scheduler import (
    WorkflowCancelled,
    WorkflowNodeResult,
    WorkflowWaitingForApproval,
)

_TERMINAL = {
    ToolInvocationStatus.COMPLETED,
    ToolInvocationStatus.REJECTED,
    ToolInvocationStatus.FAILED,
}


def execute_tool_step(adapter, node, claim, cancellation, turn):
    if node.payload["scope_digest"] != turn.scope_digest:
        raise WorkflowFenceError("Tool step scope is no longer current")
    if node.kind == ASSISTANT_STEP_TOOL_KIND:
        return _execute_invocation(adapter, node, claim, cancellation, turn)
    with adapter._factory() as unit:
        by_id = {str(item.id): item for item in unit.assistant.list_tool_invocations(turn.id)}
        ordered = tuple(by_id.get(key) for key in node.payload["invocation_ids"])
        if (
            not ordered
            or len(ordered) != len({item.id for item in ordered if item})
            or any(
                item is None
                or item.task_id != turn.task_id
                or item.scope_digest != turn.scope_digest
                or item.model_round != node.payload["model_round"]
                or item.status not in _TERMINAL
                for item in ordered
            )
            or tuple(item.sequence for item in ordered)
            != tuple(sorted(item.sequence for item in ordered))
        ):
            raise WorkflowFenceError("Tool join contains foreign or unsettled invocations")
        checkpoint = {"turn_id": str(turn.id), "invocation_ids": node.payload["invocation_ids"]}
        unit.workflows.record_checkpoint(claim, result=checkpoint)
        unit.commit()
    approval = adapter._application._changeset_approval_state(turn.id)
    if approval == "waiting":
        raise WorkflowWaitingForApproval(checkpoint)
    if approval == "rejected":
        raise WorkflowCancelled
    if turn.status is AssistantTurnStatus.WAITING_FOR_TOOL:
        if not adapter._application._resume_after_tools(turn.id):
            raise WorkflowCancelled
    elif turn.status is not AssistantTurnStatus.RUNNING:
        raise WorkflowFenceError("Tool join cannot resume this Turn state")
    child = step_node(
        node,
        STEP_MODEL,
        model_round=node.payload["model_round"] + 1,
        **{
            key: node.payload[key]
            for key in (
                "usage",
                "chunk_index",
                "feedback",
                "verification_issues",
                "invalid_tool_retry_used",
            )
        },
    )
    return WorkflowNodeResult(
        output=checkpoint,
        next_nodes=(child,),
        evidence_refs=tuple(
            f"assistant-evidence:{receipt.id}"
            for item in ordered
            for receipt in item.evidence_receipts
        ),
        public_summary="Tool results joined in original call order",
    )


def _execute_invocation(adapter, node, claim, cancellation, turn):
    invocation_id = UUID(node.payload["invocation_id"])
    checkpoint = {"turn_id": str(turn.id), "invocation_id": str(invocation_id)}
    with adapter._factory() as unit:
        invocation = unit.assistant.get_tool_invocation(invocation_id)
        if (
            invocation is None
            or invocation.turn_id != turn.id
            or invocation.task_id != turn.task_id
            or invocation.scope_digest != turn.scope_digest
        ):
            raise WorkflowFenceError("Tool node does not own this Invocation")
        unit.workflows.record_checkpoint(claim, result=checkpoint)
        unit.commit()
    app = adapter._application

    def receive_images(images):
        app._image_attachments.tool_images.put(
            turn.id,
            turn.task_id,
            node.plan_revision,
            invocation.sequence,
            images,
        )

    if invocation.status is ToolInvocationStatus.CREATED:
        definition = app._registry.get(invocation.tool_name)
        if node.payload["interpretation_revision"] != turn.active_interpretation_revision:
            _reject_prepared(adapter, invocation_id, "EXECUTION_INTENT_CHANGED")
        elif (
            definition is None or definition.definition_digest != node.payload["definition_digest"]
        ):
            _reject_prepared(adapter, invocation_id, "MCP_SCHEMA_CHANGED")
        else:
            candidate = ToolCandidate(
                call_id=invocation.provider_call_id,
                name=invocation.tool_name,
                argument_fragments=[json.dumps(invocation.arguments)],
            )
            _, message, error, images = app._execute_candidate(
                turn_id=turn.id,
                candidate=candidate,
                model_round=invocation.model_round,
                sequence=invocation.sequence,
                cancellation=cancellation,
                offered_definitions={definition.name: definition},
                prepared_invocation_id=invocation.id,
            )
            receive_images(images)
            if error is not None:
                content = None
                if message is not None:
                    # Only unwrap the envelope just produced by Core, never tool metadata.
                    pieces = message.content.split("\n", 2)
                    if len(pieces) == 3:
                        content = pieces[2].rsplit("\n", 1)[0]
                _reject_prepared(adapter, invocation_id, error, content)
    elif invocation.status in {ToolInvocationStatus.QUEUED, ToolInvocationStatus.RUNNING}:
        app._resume_pending_tool(
            turn.id,
            cancellation,
            invocation_id=invocation_id,
            image_receiver=receive_images,
        )
    with adapter._factory() as unit:
        invocation = unit.assistant.get_tool_invocation(invocation_id)
        command = (
            unit.commands.get_run(invocation.command_run_id) if invocation.command_run_id else None
        )
    if invocation.status in _TERMINAL:
        return WorkflowNodeResult(
            output=checkpoint,
            public_summary="Durable tool result recorded",
            evidence_refs=tuple(
                f"assistant-evidence:{receipt.id}" for receipt in invocation.evidence_receipts
            ),
        )
    if invocation.status is ToolInvocationStatus.CANCELLED:
        raise WorkflowCancelled
    if command is not None and command.status is CommandStatus.WAITING_APPROVAL:
        raise WorkflowWaitingForApproval(checkpoint)
    if command is not None and command.status is CommandStatus.RUNNING:
        return WorkflowNodeResult(
            output=checkpoint,
            available_at=max(
                command.lease_until or datetime.now(UTC),
                datetime.now(UTC),
            )
            + timedelta(milliseconds=100),
            public_summary="Waiting for the previous tool attempt to settle",
        )
    raise RuntimeError("Tool node did not reach a durable result or wait state")


def _reject_prepared(adapter, invocation_id, error, content=None):
    with adapter._factory() as unit:
        invocation = unit.assistant.get_tool_invocation(invocation_id)
        if invocation.status is ToolInvocationStatus.CREATED:
            invocation.reject(error_code=error, model_content=content)
            unit.assistant.update_tool_invocation(
                invocation,
                expected_status=ToolInvocationStatus.CREATED,
            )
            unit.commit()
