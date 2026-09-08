from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fairy_core.assistant.command_leases import assistant_command_lease_until
from fairy_core.assistant.model_boundary import AssistantModelYield
from fairy_core.assistant.models import AssistantTurnStatus
from fairy_core.assistant.tools import DuplicateToolCandidateError, ToolCandidateError
from fairy_core.assistant.workflow_step_nodes import (
    STEP_FINALIZE,
    STEP_MODEL,
    STEP_REVIEW,
    STEP_ROUTE,
    STEP_VERIFY,
    step_node,
)
from fairy_core.assistant.workflow_tool_checkpoint import (
    prepare_tool_checkpoint,
    restore_tool_checkpoint,
)
from fairy_core.assistant.workflow_tool_plan import (
    ASSISTANT_STEP_JOIN_KIND,
    ASSISTANT_STEP_TOOL_KIND,
)
from fairy_core.commanding import CommandStatus
from fairy_core.providers import ModelMessage, ModelRole, ProviderCancelledError
from fairy_core.workflow.errors import WorkflowFenceError
from fairy_core.workflow.models import WorkflowNodeStatus
from fairy_core.workflow.scheduler import WorkflowCancelled, WorkflowNodeResult


class _ModelBoundary:
    def __init__(self, node, claim, factory, cancellation, application):
        self.node = node
        self.claim = claim
        self.factory = factory
        self.cancellation = cancellation
        self.application = application
        self.model_round = int(node.payload["model_round"])
        self.usage = dict(node.payload.get("usage", {}))
        self.chunk_index = int(node.payload.get("chunk_index", 0))
        self.feedback = tuple(node.payload.get("feedback", ()))
        self.invalid_tool_retry_used = bool(node.payload.get("invalid_tool_retry_used", False))
        self.checkpoint = None
        with factory() as unit:
            turn = unit.assistant.get_turn(UUID(node.payload["turn_id"]))
        self.turn = turn
        self.images = application._image_attachments.tool_images.take(
            turn.id,
            turn.task_id,
            node.plan_revision,
        )

    def draft(self, **payload):
        checkpoint = {
            "type": "draft",
            "turn_id": str(payload["turn_id"]),
            "model_command_id": str(payload["run"].id),
            "content": payload["content"],
            "model_round": payload["model_round"],
            "usage": dict(payload["usage"]),
            "chunk_index": payload["chunk_index"],
            "cited_evidence_receipt_ids": list(payload["cited_evidence_receipt_ids"] or ()),
            "citations_explicit": self.node.payload.get(
                "citations_explicit",
                payload["cited_evidence_receipt_ids"] is not None,
            ),
            "reviewed": self.node.kind == STEP_REVIEW,
            "published": payload["published"],
            "feedback": list(self.feedback),
            "verification_issues": list(self.node.payload.get("verification_issues", ())),
            "invalid_tool_retry_used": self.invalid_tool_retry_used,
        }
        if (
            self.node.kind == STEP_MODEL
            and self.turn.routing_decision is not None
            and self.turn.routing_decision.reviewer_model_id is not None
        ):
            checkpoint["review_source"] = [
                {"role": message.role.value, "content": message.content}
                for message in self.application._review_source_messages(payload["source_messages"])
            ]
        self._save(checkpoint)

    def _save(self, checkpoint):
        try:
            with self.factory() as unit:
                # Existing reply limits count Unicode characters, not UTF-8 bytes.
                # This trusted adapter allowance is not supplied by model arguments.
                unit.workflows.record_checkpoint(
                    self.claim, result=checkpoint, max_bytes=8 * 1024 * 1024,
                )
                unit.commit()
        except WorkflowFenceError:
            self.cancellation.interrupt()
            raise ProviderCancelledError("Model node lost its Workflow fence") from None
        self.checkpoint = checkpoint
        raise AssistantModelYield

    def tools(self, **payload):
        try:
            self.checkpoint = prepare_tool_checkpoint(self, self.application, payload)
        except WorkflowFenceError:
            self.cancellation.interrupt()
            raise ProviderCancelledError("Tool plan lost its Workflow fence") from None
        except ToolCandidateError as error:
            if self.invalid_tool_retry_used:
                raise
            self.retry(
                **{
                    key: payload[key]
                    for key in (
                        "turn_id",
                        "run",
                        "model_round",
                        "usage",
                        "chunk_index",
                    )
                },
                error_code=(
                    "DUPLICATE_TOOL_CALL" if isinstance(error, DuplicateToolCandidateError)
                    else "PROVIDER_PROTOCOL_ERROR"
                ),
                invalid_tool_retry_used=True,
                feedback=(
                    "This plan already attempted that tool call. Use its recorded result; "
                    "do not repeat its arguments or reuse its call ID."
                    if isinstance(error, DuplicateToolCandidateError) else
                    "Use only offered tools with arguments matching their JSON schema."
                ),
            )
        raise AssistantModelYield

    def retry(self, **payload):
        self._save(
            {
                "type": "retry",
                "turn_id": str(payload["turn_id"]),
                "model_command_id": str(payload["run"].id),
                "model_round": payload["model_round"],
                "error_code": payload["error_code"],
                "feedback": [*self.feedback, payload["feedback"]],
                "usage": dict(payload["usage"]),
                "chunk_index": payload["chunk_index"],
                "invalid_tool_retry_used": payload["invalid_tool_retry_used"],
                "verification_issues": list(self.node.payload.get("verification_issues", ())),
            }
        )


class AssistantStepWorkflowAdapter:
    """Version-4 execution; opt-in until all recovery and domain gates pass."""

    def __init__(self, application, ledger, factory, preparation_adapter, *, failure_handler):
        self._application = application
        self._ledger = ledger
        self._factory = factory
        self._preparation = preparation_adapter
        self._failure_handler = failure_handler

    def execute(self, node, cancellation):
        raise WorkflowFenceError("Version-4 nodes require a live Workflow claim")

    def paused_reconciliation_phase(self, kind):
        from fairy_core.assistant.workflow_tool_steps import MEDIA_RECONCILIATION_PHASE

        return MEDIA_RECONCILIATION_PHASE if kind == ASSISTANT_STEP_TOOL_KIND else None

    def heartbeat(self, node):
        return self._ledger.renew_turn_command_leases(
            UUID(node.payload["turn_id"]),
            lease_until=assistant_command_lease_until(),
            worker_id=self._application._command_worker_id,
        )

    def execute_claimed(self, node, claim, cancellation):
        turn_id = UUID(str(node.payload["turn_id"]))
        with self._factory() as unit:
            turn = unit.assistant.get_turn(turn_id)
            if (
                turn is None
                or turn.workflow_run_id != node.run_id
                or turn.execution_engine_version != 4
                or claim.node_id != node.id
                or claim.run_id != node.run_id
            ):
                raise WorkflowFenceError("Model step does not belong to this Workflow Turn")
            renewed = unit.workflows.renew(
                claim, lease_until=assistant_command_lease_until(),
                on_failed=lambda run: self._failure_handler(unit, run),
            )
            unit.commit()
        if not renewed:
            raise WorkflowFenceError("Workflow model step lost its lease")
        cancellation.raise_if_cancelled()
        if turn.status is AssistantTurnStatus.CANCELLED:
            raise WorkflowCancelled
        from fairy_core.assistant.workflow_tool_steps import can_reconcile_while_paused

        if not can_reconcile_while_paused(self, node, turn):
            self._application._raise_if_workflow_paused(turn_id)
        if node.kind == STEP_ROUTE:
            prepared = self._application.prepare_turn(turn_id, cancellation)
            self._preparation._raise_preparation_boundary(prepared)
            with self._factory() as unit:
                run = unit.workflows.get_run(node.run_id)
            child = step_node(node, STEP_MODEL, model_round=run.model_rounds_used + 1)
            return WorkflowNodeResult(
                output={"turn_id": str(turn_id)},
                next_nodes=(child,),
                public_summary="Governed route prepared",
            )
        if node.kind in {STEP_MODEL, STEP_REVIEW}:
            checkpoint = node.result
            if checkpoint is None:
                boundary = _ModelBoundary(
                    node,
                    claim,
                    self._factory,
                    cancellation,
                    self._application,
                )
                try:
                    if node.kind == STEP_REVIEW:
                        draft = self._draft(node)
                        stopped = self._application._review_and_complete(
                            turn_id=turn_id,
                            decision=turn.routing_decision,
                            source_messages=tuple(
                                ModelMessage.create(
                                    role=ModelRole(item["role"]),
                                    content=item["content"],
                                )
                                for item in draft["review_source"]
                            ),
                            draft=draft["content"],
                            model_round=boundary.model_round,
                            chunk_index=boundary.chunk_index,
                            usage=boundary.usage,
                            cancellation=cancellation,
                            boundary=boundary,
                            cited_evidence_receipt_ids=tuple(draft["cited_evidence_receipt_ids"]),
                        )
                    else:
                        stopped = self._application.run_turn(
                            turn_id,
                            cancellation,
                            boundary=boundary,
                        )
                except AssistantModelYield:
                    checkpoint = boundary.checkpoint
                else:
                    if stopped.status is AssistantTurnStatus.CANCELLED:
                        raise WorkflowCancelled
                    self._preparation._raise_preparation_boundary(stopped)
                    raise RuntimeError(stopped.error_code or "Model step did not yield")
                finally:
                    from fairy_core.perception.tool_images import zero_images

                    zero_images(boundary.images)
            if checkpoint is not None and checkpoint.get("type") == "retry":
                return self._retry_result(node, turn, checkpoint)
            if checkpoint is not None and checkpoint.get("type") == "tools":
                nodes, edges = restore_tool_checkpoint(node, checkpoint)
                return WorkflowNodeResult(
                    output=dict(checkpoint),
                    next_nodes=nodes,
                    next_edges=edges,
                    public_summary="Governed tool nodes prepared",
                )
            if checkpoint is None or checkpoint.get("type") != "draft":
                raise RuntimeError("Model checkpoint is unavailable")
            return WorkflowNodeResult(
                output=dict(checkpoint),
                next_nodes=(step_node(node, STEP_VERIFY, model_node_id=str(node.id)),),
                public_summary="Model draft recorded",
            )
        if node.kind in {ASSISTANT_STEP_TOOL_KIND, ASSISTANT_STEP_JOIN_KIND}:
            from fairy_core.assistant.workflow_tool_steps import execute_tool_step

            return execute_tool_step(self, node, claim, cancellation, turn)
        draft = self._draft(node)
        if node.kind == STEP_VERIFY:
            decision = node.result
            if decision is None:
                issue = self._application._execution_completion_issue(
                    turn_id,
                    candidate_content=draft["content"],
                    cited_evidence_receipt_ids=(
                        tuple(draft["cited_evidence_receipt_ids"])
                        if draft.get("citations_explicit", True)
                        else None
                    ),
                )
                issues = draft.get("verification_issues", [])
                if issue is not None and (issue in issues or len(issues) >= 3):
                    from fairy_core.assistant.application import _completion_error_code

                    command = self._command(turn, draft)
                    self._application._fail_turn(
                        turn_id,
                        command,
                        error_code=_completion_error_code(issue),
                    )
                    from fairy_core.assistant.workflow_adapter import AssistantWorkflowError

                    raise AssistantWorkflowError(_completion_error_code(issue))
                decision = (
                    {"type": "verified"}
                    if issue is None
                    else {
                        **{
                            key: draft[key]
                            for key in (
                                "turn_id",
                                "model_command_id",
                                "model_round",
                                "usage",
                                "chunk_index",
                                "published",
                                "invalid_tool_retry_used",
                            )
                        },
                        "type": "retry",
                        "error_code": "WORKER_INTERRUPTED",
                        "feedback": [*draft.get("feedback", []), issue],
                        "verification_issues": [*issues, issue],
                    }
                )
                with self._factory() as unit:
                    unit.workflows.record_checkpoint(claim, result=decision)
                    unit.commit()
            if decision.get("type") == "retry":
                return self._retry_result(node, turn, decision)
            if (
                turn.routing_decision is not None
                and turn.routing_decision.reviewer_model_id
                and not draft.get("reviewed")
            ):
                command = self._command(turn, draft)
                if command.status is CommandStatus.RUNNING:
                    self._application._complete_model_round(command, output={"draft_ready": True})
                elif command.status is not CommandStatus.SUCCEEDED:
                    raise RuntimeError("Primary draft Command cannot be reviewed")
                child = step_node(
                    node,
                    STEP_REVIEW,
                    model_node_id=node.payload["model_node_id"],
                    model_round=draft["model_round"] + 1,
                    **{
                        key: draft[key]
                        for key in (
                            "usage",
                            "chunk_index",
                            "feedback",
                            "verification_issues",
                            "invalid_tool_retry_used",
                            "citations_explicit",
                        )
                    },
                )
                return WorkflowNodeResult(
                    output=dict(decision),
                    next_nodes=(child,),
                    public_summary="Verified primary candidate ready for review",
                )
            return WorkflowNodeResult(
                output=dict(decision),
                next_nodes=(
                    step_node(
                        node,
                        STEP_FINALIZE,
                        model_node_id=node.payload["model_node_id"],
                    ),
                ),
                public_summary="Response contract verified",
            )
        if node.kind != STEP_FINALIZE:
            raise ValueError("Unsupported Assistant step kind")
        command = self._command(turn, draft)
        completed = self._application._complete_turn(
            turn_id=turn_id,
            run=command,
            content=draft["content"],
            usage=dict(draft["usage"]),
            cited_evidence_receipt_ids=tuple(draft["cited_evidence_receipt_ids"]),
            workflow_claim=claim,
        )
        self._application._image_attachments.release(turn_id)
        return WorkflowNodeResult(
            output={"turn_id": str(turn_id), "status": completed.status.value},
            public_summary="Unique Assistant response finalized",
        )

    def _command(self, turn, checkpoint):
        with self._factory() as unit:
            command = unit.commands.get_run(UUID(checkpoint["model_command_id"]))
            if (
                command is None
                or command.command_name != "model.generate"
                or command.task_id != turn.task_id
                or command.conversation_id != turn.conversation_id
                or command.scope_digest != turn.scope_digest
                or command.input_payload.get("turn_id") != str(turn.id)
            ):
                raise WorkflowFenceError("Model Command does not match its checkpoint Turn")
            if command.status is CommandStatus.RUNNING and (
                command.lease_until is not None and command.lease_until <= datetime.now(UTC)
            ):
                command = self._application._command_bus(unit.commands).start(
                    command.id,
                    lease_until=assistant_command_lease_until(),
                )
                unit.commit()
            if command.status is CommandStatus.RUNNING and (
                command.lease_owner != self._application._command_worker_id
                or command.lease_until is None
            ):
                raise WorkflowFenceError("Model checkpoint is owned by another live worker")
        return command

    def _retry_result(self, node, turn, checkpoint):
        command = self._command(turn, checkpoint)
        if command.status is CommandStatus.RUNNING:
            if checkpoint.get("published"):
                self._application._reset_message_projection(
                    turn_id=turn.id,
                    run=command,
                    through_chunk_index=checkpoint["chunk_index"],
                    reason="execution_incomplete",
                )
            self._application._reject_model_round_for_retry(
                command,
                error_code=checkpoint["error_code"],
                public_detail="Fairy is preparing the next governed model round.",
            )
        elif command.status is not CommandStatus.FAILED:
            raise RuntimeError("Retry checkpoint model Command has an unexpected state")
        child = step_node(
            node,
            STEP_MODEL,
            model_round=int(checkpoint["model_round"]) + 1,
            usage=checkpoint["usage"],
            chunk_index=checkpoint["chunk_index"],
            feedback=checkpoint["feedback"],
            verification_issues=checkpoint.get("verification_issues", []),
            invalid_tool_retry_used=checkpoint.get("invalid_tool_retry_used", False),
        )
        return WorkflowNodeResult(
            output=dict(checkpoint),
            next_nodes=(child,),
            public_summary="Next model round prepared",
        )

    def _draft(self, node):
        with self._factory() as unit:
            source = unit.workflows.get_node(node.run_id, UUID(node.payload["model_node_id"]))
        if (
            source is None
            or source.kind not in {STEP_MODEL, STEP_REVIEW}
            or source.plan_revision != node.plan_revision
            or source.status is not WorkflowNodeStatus.SUCCEEDED
            or source.payload["turn_id"] != node.payload["turn_id"]
            or source.result is None
            or source.result.get("type") != "draft"
        ):
            raise WorkflowFenceError("Draft belongs to a different or unfinished model step")
        return source.result

    def replan_after_pause(self, node):
        return self._preparation.replan_after_pause(node)

    def settle_failure_in_unit(self, unit, node, claim, error_code):
        # Called only after Kernel.fail accepted this live Fence, in the same transaction.
        turn = unit.assistant.get_turn(UUID(node.payload["turn_id"]))
        if (
            turn is None
            or turn.workflow_run_id != claim.run_id
            or node.run_id != claim.run_id
            or turn.execution_engine_version != 4
        ):
            return
        if turn.is_terminal:
            return
        command = None
        for step in reversed(unit.assistant.list_trace_steps(turn.id)):
            if step.command_run_id is None:
                continue
            candidate = unit.commands.get_run(step.command_run_id)
            if (
                candidate is not None
                and candidate.status is CommandStatus.RUNNING
                and candidate.command_name == "model.generate"
                and candidate.input_payload.get("turn_id") == str(turn.id)
                and candidate.task_id == turn.task_id
                and candidate.conversation_id == turn.conversation_id
                and candidate.scope_digest == turn.scope_digest
                and candidate.lease_until is not None
                and candidate.lease_until <= datetime.now(UTC)
            ):
                # A node Fence does not confer the current Command owner's lease.
                # Reclaim only an expired model command, solely for settlement;
                # never replay its Provider request or borrow a live worker's Fence.
                command = self._application._command_bus(unit.commands).start(
                    candidate.id, lease_until=assistant_command_lease_until(),
                )
                break
        self._application._fail_turn_in_unit(unit, turn.id, command, error_code=error_code)
