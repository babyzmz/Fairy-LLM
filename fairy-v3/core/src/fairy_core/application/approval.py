from __future__ import annotations

from uuid import UUID

from fairy_core.commanding.models import CommandStatus, EventVisibility
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.execution import (
    Approval,
    ApprovalDecision,
    ChangesetStatus,
)
from fairy_core.domain.models import TaskStatus
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory


class ApprovalApplication:
    """Records a user decision and queues or rejects its CommandRun without effects."""

    def __init__(self, *, unit_of_work_factory: CoreUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def get(self, approval_id: UUID) -> Approval:
        with self._unit_of_work_factory() as unit_of_work:
            approval = unit_of_work.state.get_approval(approval_id)
        if approval is None:
            raise KeyError(f"approval not found: {approval_id}")
        return approval

    def decide(
        self,
        *,
        approval_id: UUID,
        approved: bool,
        decided_by: str,
    ) -> Approval:
        decision = ApprovalDecision.APPROVED if approved else ApprovalDecision.REJECTED
        with self._unit_of_work_factory() as unit_of_work:
            approval = unit_of_work.state.get_approval(approval_id)
            if approval is None:
                raise KeyError(f"approval not found: {approval_id}")
            if approval.decision is not ApprovalDecision.PENDING:
                if approval.decision is decision:
                    return approval
                raise InvalidTransitionError("approval has already been decided differently")

            command = unit_of_work.commands.get_run(approval.command_run_id)
            if command is None:
                raise KeyError(f"command run not found: {approval.command_run_id}")
            if command.status is not CommandStatus.WAITING_APPROVAL:
                raise InvalidTransitionError(
                    f"approval CommandRun cannot be decided from {command.status.value}"
                )
            if approval.tool_invocation_id is not None:
                invocation = unit_of_work.assistant.get_tool_invocation(approval.tool_invocation_id)
                if invocation is None or invocation.command_run_id != command.id:
                    raise InvalidTransitionError(
                        "approval Tool Invocation does not match its CommandRun"
                    )
                task = unit_of_work.state.get_task(approval.task_id)
                if task is None:
                    raise KeyError(f"task not found: {approval.task_id}")
                if task.status is not TaskStatus.AWAITING_APPROVAL:
                    raise InvalidTransitionError("Assistant Task is not awaiting approval")
                task.transition_to(TaskStatus.EXECUTING)
                unit_of_work.state.save_task(task)

            expected_decision = approval.decision
            approval.decide(decision=decision, decided_by=decided_by)
            if approval.changeset_id is not None:
                changeset = unit_of_work.state.get_changeset(approval.changeset_id)
                if changeset is None:
                    raise KeyError(f"changeset not found: {approval.changeset_id}")
                changeset.record_approval(decision)
                unit_of_work.state.save_changeset(changeset)
                if not approved:
                    changeset.transition_to(ChangesetStatus.REJECTED)
                    task = unit_of_work.state.get_task(approval.task_id)
                    if task is None:
                        raise KeyError(f"task not found: {approval.task_id}")
                    if task.status is not TaskStatus.AWAITING_APPROVAL:
                        raise InvalidTransitionError("Changeset Task is not awaiting approval")
                    task.transition_to(TaskStatus.REJECTED)
                    unit_of_work.state.save_changeset(changeset)
                    unit_of_work.state.save_task(task)

            unit_of_work.commands.append_event(
                run_id=command.id,
                event_type="approval.decided",
                visibility=EventVisibility.USER,
                message="Approval decision recorded",
                payload={
                    "approval_id": str(approval.id),
                    "decision": decision.value,
                },
            )
            unit_of_work.commands.transition(
                command.id,
                CommandStatus.QUEUED if approved else CommandStatus.REJECTED,
            )
            unit_of_work.state.update_approval(
                approval,
                expected_decision=expected_decision,
            )
            unit_of_work.commit()
        return approval


__all__ = ["ApprovalApplication"]
