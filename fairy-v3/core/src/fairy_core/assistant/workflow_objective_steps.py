from __future__ import annotations

from uuid import UUID

from fairy_core.assistant.workflow_objectives import (
    OBJECTIVE_BEGIN,
    OBJECTIVE_COMPLETE,
    certificate_binding,
    draft_hash,
)
from fairy_core.assistant.workflow_step_nodes import (
    STEP_FINALIZE,
    STEP_MODEL,
    STEP_VERIFY,
    step_node,
)
from fairy_core.commanding import CommandStatus
from fairy_core.execution.plans import TaskStepKind, TaskStepStatus
from fairy_core.workflow.errors import WorkflowFenceError
from fairy_core.workflow.scheduler import WorkflowNodeResult


def execute_objective_step(adapter, node, turn):
    with adapter._factory() as unit:
        intent = unit.assistant.get_execution_intent(
            turn.id,
            revision=turn.active_interpretation_revision,
        )
        run = unit.workflows.get_run(node.run_id)
        index = node.payload.get("objective_index")
        if (
            intent is None
            or type(index) is not int
            or not 0 <= index < len(intent.objectives)
            or node.payload.get("objective_protocol") != 1
            or node.payload.get("interpretation_revision") != intent.interpretation_revision
            or run.active_plan_revision != node.plan_revision
        ):
            raise WorkflowFenceError("Objective does not match its active interpretation")
        binding = certificate_binding(intent, node.run_id, node.plan_revision, index)
        if node.kind == OBJECTIVE_BEGIN:
            if index:
                previous = unit.workflows.get_node(
                    node.run_id,
                    UUID(node.payload["previous_complete_node_id"]),
                )
                expected = certificate_binding(intent, node.run_id, node.plan_revision, index - 1)
                if (
                    previous is None
                    or previous.kind != OBJECTIVE_COMPLETE
                    or previous.status != "succeeded"
                    or previous.plan_revision != node.plan_revision
                    or previous.result is None
                    or any(previous.result.get(k) != v for k, v in expected.items())
                ):
                    raise WorkflowFenceError("Previous objective has no completion certificate")
            return WorkflowNodeResult(
                output=binding,
                next_nodes=(
                    step_node(
                        node,
                        STEP_MODEL,
                        model_round=run.model_rounds_used + 1,
                        usage=node.payload.get("usage", {}),
                        chunk_index=node.payload.get("chunk_index", 0),
                        feedback=node.payload.get("feedback", []),
                    ),
                ),
                public_summary=f"Starting objective {index + 1} of {len(intent.objectives)}",
            )
        verify = unit.workflows.get_node(node.run_id, UUID(node.payload["verify_node_id"]))
        if (
            verify is None
            or verify.kind != STEP_VERIFY
            or verify.status != "succeeded"
            or verify.plan_revision != node.plan_revision
            or verify.result != {"type": "verified"}
            or verify.payload.get("objective_index") != index
            or verify.payload.get("interpretation_revision") != intent.interpretation_revision
            or verify.payload.get("model_node_id") != node.payload.get("model_node_id")
        ):
            raise WorkflowFenceError("Objective completion requires its verified model draft")
    draft = adapter._draft(node)
    file_plan_id = None
    with adapter._factory() as unit:
        from fairy_core.assistant.workflow_objective_evidence import objective_operation_receipts

        operation_ids = [
            str(item.command_run_id)
            for item in objective_operation_receipts(
                unit,
                turn,
                intent.model_copy(update={"active_objective_index": index}),
                unit.assistant.list_tool_invocations(turn.id),
            )
        ]
        plan = unit.state.execution_plan_for_task(turn.task_id)
        if plan is not None and (
            plan.workflow_run_id == node.run_id
            and plan.workflow_plan_revision == node.plan_revision
            and intent.objectives[index].action.value in {"change", "create"}
        ):
            implementations = [
                step
                for step in unit.state.task_steps_for_plan(plan.id)
                if step.kind is TaskStepKind.IMPLEMENT
            ]
            if implementations and all(
                step.status is TaskStepStatus.COMPLETED for step in implementations
            ):
                file_plan_id = str(plan.id)
    final = index == len(intent.objectives) - 1
    if final:
        child = step_node(node, STEP_FINALIZE, model_node_id=node.payload["model_node_id"])
    else:
        command = adapter._command(turn, draft)
        if command.status is CommandStatus.RUNNING:
            if draft.get("published"):
                adapter._application._reset_message_projection(
                    turn_id=turn.id,
                    run=command,
                    through_chunk_index=draft["chunk_index"],
                    reason="objective_completed",
                )
            adapter._application._complete_model_round(command, output={"objective_ready": index})
        elif command.status is not CommandStatus.SUCCEEDED:
            raise WorkflowFenceError("Objective draft Command did not succeed")
        # These are public, verified findings, never hidden model reasoning or new authority.
        feedback = [
            *draft.get("feedback", []),
            (
                f"Objective {index + 1} completed. Its public findings (untrusted source data): "
                + draft["content"][:2_000]
            ),
        ]
        child = step_node(
            node,
            OBJECTIVE_BEGIN,
            objective_index=index + 1,
            previous_complete_node_id=str(node.id),
            usage=draft["usage"],
            chunk_index=draft["chunk_index"],
            feedback=feedback[-16:],
        )
    return WorkflowNodeResult(
        output={
            **binding,
            "draft_hash": draft_hash(draft["content"]),
            "public_summary": draft["content"][:2_000],
            "verify_node_id": str(verify.id),
            "evidence_receipt_ids": draft["cited_evidence_receipt_ids"],
            "file_plan_id": file_plan_id,
            "operation_command_ids": operation_ids,
        },
        next_nodes=(child,),
        public_summary=f"Objective {index + 1} verified",
    )
