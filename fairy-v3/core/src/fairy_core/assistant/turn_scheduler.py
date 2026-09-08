from __future__ import annotations

import time
from uuid import UUID

from fairy_core.assistant.ledger import AssistantLedgerApplication
from fairy_core.assistant.models import AssistantTurn, AssistantTurnStatus
from fairy_core.workflow.models import WorkflowRunStatus
from fairy_core.workflow.scheduler import WorkflowScheduler


class AssistantTurnScheduler:
    """Assistant compatibility facade backed only by the Workflow Kernel."""

    def __init__(
        self,
        *,
        ledger: AssistantLedgerApplication,
        workflow_scheduler: WorkflowScheduler,
    ) -> None:
        self._ledger = ledger
        self._workflow_scheduler = workflow_scheduler

    def close(self) -> None:
        return None

    def cancel(self, turn_id: UUID) -> bool:
        turn = self._ledger.get_turn(turn_id)
        if turn.workflow_run_id is None:
            self._reject_unbound_nonterminal(turn)
            return False
        if turn.workflow_summary is None or turn.workflow_summary.status in {
            WorkflowRunStatus.COMPLETED,
            WorkflowRunStatus.CANCELLED,
            WorkflowRunStatus.FAILED,
        }:
            return False
        self._workflow_scheduler.cancel(turn.workflow_run_id)
        return True

    def pause(self, turn_id: UUID) -> AssistantTurn:
        turn = self._require_workflow_turn(turn_id)
        assert turn.workflow_run_id is not None
        self._workflow_scheduler.pause(turn.workflow_run_id)
        return self._ledger.get_turn(turn_id)

    def resume(self, turn_id: UUID) -> AssistantTurn:
        turn = self._require_workflow_turn(turn_id)
        assert turn.workflow_run_id is not None
        self._workflow_scheduler.resume(turn.workflow_run_id)
        return self._ledger.get_turn(turn_id)

    def steer(
        self,
        *,
        turn_id: UUID,
        instruction: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> AssistantTurn:
        turn = self._ledger.steer_turn(
            turn_id=turn_id,
            instruction=instruction,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )
        self._workflow_scheduler.wake()
        return turn

    def respond(
        self,
        *,
        turn_id: UUID,
        content: str,
        expected_interpretation_revision: int,
        idempotency_key: str,
    ) -> AssistantTurn:
        was_waiting_for_input = (
            self._ledger.get_turn(turn_id).status is AssistantTurnStatus.WAITING_FOR_INPUT
        )
        turn = self._ledger.respond_to_clarification(
            turn_id=turn_id,
            content=content,
            expected_interpretation_revision=expected_interpretation_revision,
            idempotency_key=idempotency_key,
        )
        assert turn.workflow_run_id is not None
        if was_waiting_for_input:
            self._workflow_scheduler.resume_after_boundary(turn.workflow_run_id)
        return turn

    def run(self, turn_id: UUID) -> AssistantTurn:
        turn = self._ledger.get_turn(turn_id)
        if turn.status in _TERMINAL_TURN_STATUSES:
            return turn
        turn = self._require_workflow_turn(turn_id)
        self._start_workflow(turn)
        assert turn.workflow_run_id is not None
        deadline = time.monotonic() + 2 * 60 * 60
        snapshot = self._workflow_scheduler.wait(
            turn.workflow_run_id,
            timeout=2 * 60 * 60,
        )
        if snapshot.run.status is not WorkflowRunStatus.CANCELLED:
            return self._ledger.get_turn(turn_id)
        while True:
            turn = self._ledger.get_turn(turn_id)
            if turn.status in _TERMINAL_TURN_STATUSES and not turn.cancellation_pending:
                return turn
            if time.monotonic() >= deadline:
                raise TimeoutError("Assistant Turn did not settle with its Workflow")
            time.sleep(0.05)

    def start(
        self,
        turn_id: UUID,
        *,
        restart_if_running: bool = False,
    ) -> AssistantTurn:
        turn = self._ledger.get_turn(turn_id)
        if turn.status in _TERMINAL_TURN_STATUSES:
            return turn
        turn = self._require_workflow_turn(turn_id)
        if restart_if_running:
            assert turn.workflow_run_id is not None
            self._workflow_scheduler.resume_after_boundary(turn.workflow_run_id)
        else:
            self._start_workflow(turn)
        return self._ledger.get_turn(turn_id)

    def _require_workflow_turn(self, turn_id: UUID) -> AssistantTurn:
        turn = self._ledger.get_turn(turn_id)
        self._reject_unbound_nonterminal(turn)
        if turn.workflow_run_id is None or turn.workflow_summary is None:
            raise RuntimeError("Assistant Workflow binding is unavailable")
        return turn

    @staticmethod
    def _reject_unbound_nonterminal(turn: AssistantTurn) -> None:
        if turn.workflow_run_id is None and turn.status not in _TERMINAL_TURN_STATUSES:
            raise RuntimeError("Non-terminal pre-Workflow Assistant Turn blocks this Core upgrade")

    def _start_workflow(self, turn: AssistantTurn) -> None:
        assert turn.workflow_run_id is not None
        summary = turn.workflow_summary
        if summary is None:
            raise RuntimeError("Assistant Workflow summary is unavailable")
        if summary.status in {
            WorkflowRunStatus.WAITING_FOR_APPROVAL,
            WorkflowRunStatus.PAUSED,
        }:
            self._workflow_scheduler.resume(turn.workflow_run_id)
        elif summary.status not in {
            WorkflowRunStatus.COMPLETED,
            WorkflowRunStatus.CANCELLED,
            WorkflowRunStatus.FAILED,
        }:
            self._workflow_scheduler.wake()


_TERMINAL_TURN_STATUSES = {
    AssistantTurnStatus.COMPLETED,
    AssistantTurnStatus.CANCELLED,
    AssistantTurnStatus.FAILED,
}


__all__ = ["AssistantTurnScheduler"]
