from __future__ import annotations

from uuid import UUID

from fairy_core.assistant.application import AssistantApplication
from fairy_core.assistant.command_leases import assistant_command_lease_until
from fairy_core.assistant.ledger import AssistantLedgerApplication
from fairy_core.assistant.models import AssistantTurnStatus
from fairy_core.assistant.workflow_plan import (
    ASSISTANT_WORKFLOW_NODE_KIND,
    ASSISTANT_WORKFLOW_PREPARE_NODE_KIND,
    apply_pending_assistant_steering,
)
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
        )

    def execute(
        self,
        node: WorkflowNode,
        cancellation: CancellationToken,
    ) -> WorkflowNodeResult:
        if node.kind not in {
            ASSISTANT_WORKFLOW_NODE_KIND,
            ASSISTANT_WORKFLOW_PREPARE_NODE_KIND,
        } or set(node.payload) != {"turn_id"}:
            raise AssistantWorkflowError("ASSISTANT_WORKFLOW_PAYLOAD_INVALID")
        try:
            turn_id = UUID(str(node.payload["turn_id"]))
        except (TypeError, ValueError) as error:
            raise AssistantWorkflowError("ASSISTANT_WORKFLOW_PAYLOAD_INVALID") from error
        if node.kind == ASSISTANT_WORKFLOW_PREPARE_NODE_KIND:
            turn = self._application.prepare_turn(turn_id, cancellation)
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
            return WorkflowNodeResult(
                output={
                    "turn_id": str(turn.id),
                    "interpretation_revision": turn.active_interpretation_revision,
                    "routed": turn.routing_decision is not None,
                },
                public_summary="Fairy understood and routed the request",
            )
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
) -> None:
    adapter = AssistantTurnWorkflowAdapter(application, ledger, unit_of_work_factory)
    adapters.register(ASSISTANT_WORKFLOW_PREPARE_NODE_KIND, adapter)
    adapters.register(ASSISTANT_WORKFLOW_NODE_KIND, adapter)


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
