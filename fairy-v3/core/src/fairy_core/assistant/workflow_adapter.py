from __future__ import annotations

from uuid import UUID

from fairy_core.assistant.application import AssistantApplication
from fairy_core.assistant.ledger import (
    ASSISTANT_WORKFLOW_NODE_KIND,
    AssistantLedgerApplication,
)
from fairy_core.assistant.models import AssistantTurnStatus
from fairy_core.assistant.work_queue import assistant_command_lease_until
from fairy_core.providers import CancellationToken
from fairy_core.workflow.models import WorkflowNode
from fairy_core.workflow.scheduler import (
    WorkflowAdapterRegistry,
    WorkflowCancelled,
    WorkflowNodeResult,
    WorkflowScheduler,
    WorkflowWaitingForApproval,
)


class AssistantWorkflowError(RuntimeError):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class AssistantTurnWorkflowAdapter:
    def __init__(
        self,
        application: AssistantApplication,
        ledger: AssistantLedgerApplication,
    ) -> None:
        self._application = application
        self._ledger = ledger

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
        if node.kind != ASSISTANT_WORKFLOW_NODE_KIND or set(node.payload) != {"turn_id"}:
            raise AssistantWorkflowError("ASSISTANT_WORKFLOW_PAYLOAD_INVALID")
        try:
            turn_id = UUID(str(node.payload["turn_id"]))
        except (TypeError, ValueError) as error:
            raise AssistantWorkflowError("ASSISTANT_WORKFLOW_PAYLOAD_INVALID") from error
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


def build_assistant_workflow_scheduler(
    *,
    unit_of_work_factory,
    application: AssistantApplication,
    ledger: AssistantLedgerApplication,
) -> WorkflowScheduler:
    adapters = WorkflowAdapterRegistry()
    adapters.register(
        ASSISTANT_WORKFLOW_NODE_KIND,
        AssistantTurnWorkflowAdapter(application, ledger),
    )
    return WorkflowScheduler(unit_of_work_factory=unit_of_work_factory, adapters=adapters)


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
    "build_assistant_workflow_scheduler",
    "resumable_assistant_turn_ids",
]
