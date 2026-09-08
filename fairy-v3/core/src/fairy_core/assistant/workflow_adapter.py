from __future__ import annotations

from uuid import UUID

from fairy_core.assistant.application import AssistantApplication
from fairy_core.assistant.command_leases import assistant_command_lease_until
from fairy_core.assistant.ledger import AssistantLedgerApplication
from fairy_core.assistant.models import (
    AssistantTurn,
    AssistantTurnStatus,
    MessageRole,
    ToolInvocationStatus,
)
from fairy_core.assistant.workflow_plan import (
    ASSISTANT_CONTINUATION_NODE_KINDS,
    ASSISTANT_MODEL_ROUND_NODE_KIND,
    ASSISTANT_REQUEST_INTERPRET_NODE_KIND,
    ASSISTANT_RESPONSE_FINALIZE_NODE_KIND,
    ASSISTANT_RESPONSE_VERIFY_NODE_KIND,
    ASSISTANT_ROUTE_SELECT_NODE_KIND,
    ASSISTANT_TOOL_INVOKE_NODE_KIND,
    ASSISTANT_TOOL_JOIN_NODE_KIND,
    ASSISTANT_WORKFLOW_NODE_KIND,
    ASSISTANT_WORKFLOW_PREPARE_NODE_KIND,
    apply_pending_assistant_steering,
)
from fairy_core.assistant.workflow_settlement import AssistantWorkflowFailureProjection
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import CancellationToken
from fairy_core.workflow.models import WorkflowNode
from fairy_core.workflow.scheduler import (
    WorkflowAdapterRegistry,
    WorkflowCancelled,
    WorkflowNodeResult,
    WorkflowWaitingForApproval,
    WorkflowWaitingForInput,
)


class AssistantWorkflowError(RuntimeError):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class AssistantTurnWorkflowAdapter:
    may_wait_for_child_workflow = True

    def __init__(
        self,
        application: AssistantApplication,
        ledger: AssistantLedgerApplication,
        unit_of_work_factory: CoreUnitOfWorkFactory,
    ) -> None:
        self._application = application
        self._ledger = ledger
        self._unit_of_work_factory = unit_of_work_factory

    def heartbeat(self, node: WorkflowNode) -> bool:
        try:
            turn_id = UUID(str(node.payload["turn_id"]))
        except (KeyError, TypeError, ValueError):
            return False
        return self._ledger.renew_turn_command_leases(
            turn_id,
            lease_until=assistant_command_lease_until(),
            worker_id=self._application._command_worker_id,
        )

    def execute(
        self,
        node: WorkflowNode,
        cancellation: CancellationToken,
    ) -> WorkflowNodeResult:
        if node.kind not in {
            ASSISTANT_WORKFLOW_NODE_KIND,
            ASSISTANT_WORKFLOW_PREPARE_NODE_KIND,
            *ASSISTANT_CONTINUATION_NODE_KINDS,
        } or set(node.payload) != {"turn_id"}:
            raise AssistantWorkflowError("ASSISTANT_WORKFLOW_PAYLOAD_INVALID")
        try:
            turn_id = UUID(str(node.payload["turn_id"]))
        except (TypeError, ValueError) as error:
            raise AssistantWorkflowError("ASSISTANT_WORKFLOW_PAYLOAD_INVALID") from error
        if node.kind == ASSISTANT_WORKFLOW_PREPARE_NODE_KIND:
            turn = self._application.prepare_turn(turn_id, cancellation)
            self._raise_preparation_boundary(turn)
            return self._preparation_result(turn)
        if node.kind == ASSISTANT_REQUEST_INTERPRET_NODE_KIND:
            turn = self._application.prepare_turn(
                turn_id,
                cancellation,
                allow_budget_approval=False,
            )
            self._raise_preparation_boundary(turn)
            if turn.active_interpretation_revision is None:
                raise AssistantWorkflowError("ASSISTANT_INTERPRETATION_MISSING")
            return WorkflowNodeResult(
                output={
                    "turn_id": str(turn.id),
                    "interpretation_revision": turn.active_interpretation_revision,
                },
                public_summary="Fairy interpreted the request",
            )
        if node.kind == ASSISTANT_ROUTE_SELECT_NODE_KIND:
            turn = self._application.prepare_turn(turn_id, cancellation)
            self._raise_preparation_boundary(turn)
            return WorkflowNodeResult(
                output={
                    "turn_id": str(turn.id),
                    "routed": turn.routing_decision is not None,
                    "task_kind": (
                        turn.routing_decision.task_kind.value
                        if turn.routing_decision is not None
                        else "legacy"
                    ),
                },
                public_summary="Fairy selected the governed route",
            )
        if node.kind == ASSISTANT_MODEL_ROUND_NODE_KIND:
            return self._run_model_continuation(turn_id, cancellation)
        if node.kind == ASSISTANT_TOOL_INVOKE_NODE_KIND:
            return self._tool_invocation_checkpoint(turn_id)
        if node.kind == ASSISTANT_TOOL_JOIN_NODE_KIND:
            return self._tool_join_checkpoint(turn_id)
        if node.kind == ASSISTANT_RESPONSE_VERIFY_NODE_KIND:
            return self._response_checkpoint(turn_id, final=False)
        if node.kind == ASSISTANT_RESPONSE_FINALIZE_NODE_KIND:
            return self._response_checkpoint(turn_id, final=True)
        return self._run_model_continuation(turn_id, cancellation)

    def _run_model_continuation(
        self,
        turn_id: UUID,
        cancellation: CancellationToken,
    ) -> WorkflowNodeResult:
        turn = self._application.run_turn(turn_id, cancellation)
        if turn.status is AssistantTurnStatus.WAITING_FOR_TOOL:
            raise WorkflowWaitingForApproval(
                {
                    "turn_id": str(turn.id),
                    "turn_status": turn.status.value,
                }
            )
        if turn.status is AssistantTurnStatus.CANCELLED:
            raise WorkflowCancelled
        if turn.status is AssistantTurnStatus.FAILED:
            raise AssistantWorkflowError(turn.error_code or "ASSISTANT_INTERNAL_ERROR")
        if turn.status is not AssistantTurnStatus.COMPLETED:
            raise AssistantWorkflowError("ASSISTANT_WORKFLOW_UNSETTLED")
        return WorkflowNodeResult(
            output={
                "turn_id": str(turn.id),
                "status": turn.status.value,
                "usage": dict(turn.usage),
            },
            evidence_refs=tuple(
                f"assistant-evidence:{receipt_id}" for receipt_id in turn.cited_evidence_receipt_ids
            ),
            public_summary="Fairy completed the response",
        )

    def _raise_preparation_boundary(self, turn: AssistantTurn) -> None:
        if turn.status is AssistantTurnStatus.CANCELLED:
            raise WorkflowCancelled
        if turn.status is AssistantTurnStatus.WAITING_FOR_TOOL:
            raise WorkflowWaitingForApproval(
                {
                    "turn_id": str(turn.id),
                    "turn_status": turn.status.value,
                }
            )
        if turn.status is AssistantTurnStatus.WAITING_FOR_INPUT:
            with self._unit_of_work_factory() as unit_of_work:
                interpretation = unit_of_work.assistant.get_interpretation(
                    turn.id,
                    turn.active_interpretation_revision,
                )
            raise WorkflowWaitingForInput(
                {
                    "turn_id": str(turn.id),
                    "interpretation_revision": turn.active_interpretation_revision,
                    "clarification_question": (
                        interpretation.clarification_question
                        if interpretation is not None
                        else None
                    ),
                }
            )
        if turn.is_terminal:
            raise AssistantWorkflowError(
                turn.error_code or "ASSISTANT_PREPARATION_TERMINATED"
            )

    @staticmethod
    def _preparation_result(turn: AssistantTurn) -> WorkflowNodeResult:
        return WorkflowNodeResult(
            output={
                "turn_id": str(turn.id),
                "interpretation_revision": turn.active_interpretation_revision,
                "routed": turn.routing_decision is not None,
            },
            public_summary="Fairy understood and routed the request",
        )

    def _tool_invocation_checkpoint(self, turn_id: UUID) -> WorkflowNodeResult:
        with self._unit_of_work_factory() as unit_of_work:
            turn = unit_of_work.assistant.get_turn(turn_id)
            invocations = unit_of_work.assistant.list_tool_invocations(turn_id)
        if turn is None or turn.status is not AssistantTurnStatus.COMPLETED:
            raise AssistantWorkflowError("ASSISTANT_TOOL_BOUNDARY_UNSETTLED")
        unsettled = tuple(
            invocation
            for invocation in invocations
            if invocation.status
            in {
                ToolInvocationStatus.CREATED,
                ToolInvocationStatus.QUEUED,
                ToolInvocationStatus.RUNNING,
            }
        )
        if unsettled:
            raise AssistantWorkflowError("ASSISTANT_TOOL_INVOCATION_UNSETTLED")
        return WorkflowNodeResult(
            output={
                "turn_id": str(turn_id),
                "tool_invocation_ids": [str(invocation.id) for invocation in invocations],
            },
            public_summary="Durable tool invocations reconciled",
        )

    def _tool_join_checkpoint(self, turn_id: UUID) -> WorkflowNodeResult:
        with self._unit_of_work_factory() as unit_of_work:
            invocations = tuple(
                sorted(
                    unit_of_work.assistant.list_tool_invocations(turn_id),
                    key=lambda invocation: (invocation.model_round, invocation.sequence),
                )
            )
        return WorkflowNodeResult(
            output={
                "turn_id": str(turn_id),
                "ordered_tool_invocation_ids": [
                    str(invocation.id) for invocation in invocations
                ],
            },
            evidence_refs=tuple(
                f"assistant-evidence:{receipt_id}"
                for invocation in invocations
                for receipt_id in (receipt.id for receipt in invocation.evidence_receipts)
            ),
            public_summary="Tool results joined in model call order",
        )

    def _response_checkpoint(self, turn_id: UUID, *, final: bool) -> WorkflowNodeResult:
        with self._unit_of_work_factory() as unit_of_work:
            turn = unit_of_work.assistant.get_turn(turn_id)
            message = unit_of_work.assistant.message_for_turn(turn_id, MessageRole.ASSISTANT)
        if turn is None or turn.status is not AssistantTurnStatus.COMPLETED:
            raise AssistantWorkflowError("ASSISTANT_RESPONSE_UNSETTLED")
        if message is None or not message.content.strip():
            raise AssistantWorkflowError("ASSISTANT_RESPONSE_MISSING")
        return WorkflowNodeResult(
            output={
                "turn_id": str(turn_id),
                "message_id": str(message.id),
                "content_characters": len(message.content),
            },
            evidence_refs=tuple(
                f"assistant-evidence:{receipt_id}"
                for receipt_id in turn.cited_evidence_receipt_ids
            ),
            public_summary=(
                "Unique Assistant response finalized"
                if final
                else "Assistant response contract verified"
            ),
        )

    def replan_after_pause(self, node: WorkflowNode) -> bool:
        with self._unit_of_work_factory() as unit_of_work:
            changed = apply_pending_assistant_steering(unit_of_work, node.run_id)
            if changed:
                unit_of_work.commit()
            return changed


def register_assistant_workflow_adapter(
    adapters: WorkflowAdapterRegistry,
    *,
    unit_of_work_factory,
    application: AssistantApplication,
    ledger: AssistantLedgerApplication,
) -> AssistantWorkflowFailureProjection:
    from fairy_core.assistant.workflow_step_adapter import AssistantStepWorkflowAdapter
    from fairy_core.assistant.workflow_step_nodes import (
        STEP_FINALIZE,
        STEP_MODEL,
        STEP_REVIEW,
        STEP_ROUTE,
        STEP_VERIFY,
    )
    from fairy_core.assistant.workflow_tool_plan import (
        ASSISTANT_STEP_JOIN_KIND,
        ASSISTANT_STEP_TOOL_KIND,
    )

    adapter = AssistantTurnWorkflowAdapter(application, ledger, unit_of_work_factory)
    projection = AssistantWorkflowFailureProjection(application)
    for version in (2, 3, 4):
        adapters.register_failure_handler("assistant_turn", version, projection.settle)
    adapters.register(ASSISTANT_WORKFLOW_PREPARE_NODE_KIND, adapter)
    adapters.register(ASSISTANT_WORKFLOW_NODE_KIND, adapter)
    for kind in ASSISTANT_CONTINUATION_NODE_KINDS:
        adapters.register(kind, adapter)
    steps = AssistantStepWorkflowAdapter(
        application, ledger, unit_of_work_factory, adapter,
        failure_handler=adapters.settle_failed_run,
    )
    for kind in (STEP_ROUTE, STEP_MODEL, STEP_REVIEW, STEP_VERIFY, STEP_FINALIZE):
        adapters.register(kind, steps)
    adapters.register(ASSISTANT_STEP_JOIN_KIND, steps)
    tool_steps = AssistantStepWorkflowAdapter(
        application, ledger, unit_of_work_factory, adapter,
        failure_handler=adapters.settle_failed_run,
    )
    adapters.register(ASSISTANT_STEP_TOOL_KIND, tool_steps)
    return projection


def resumable_assistant_turn_ids(ledger: AssistantLedgerApplication) -> tuple[UUID, ...]:
    return tuple(
        sorted(
            {*ledger.resumable_workflow_turn_ids(), *ledger.resumable_waiting_turn_ids()},
            key=str,
        )
    )


__all__ = [
    "AssistantTurnWorkflowAdapter",
    "AssistantWorkflowError",
    "register_assistant_workflow_adapter",
    "resumable_assistant_turn_ids",
]
