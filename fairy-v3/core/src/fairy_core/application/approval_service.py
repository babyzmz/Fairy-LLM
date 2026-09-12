from __future__ import annotations

import json
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel

from fairy_core.application.execution_helpers import tool_result_changeset_id
from fairy_core.assistant.models import ToolInvocationStatus
from fairy_core.commanding.models import CommandStatus
from fairy_core.contracts.approvals import ApprovalDecisionInput
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.execution import Approval, ApprovalDecision, ChangesetStatus


class CoreApprovalServiceMixin:
    def _assistant_turn_id_for_approval(self, approval: Approval) -> UUID | None:
        if approval.changeset_id is not None:
            with self._unit_of_work_factory() as unit_of_work:
                changeset = unit_of_work.state.get_changeset(approval.changeset_id)
                command = unit_of_work.commands.get_run(approval.command_run_id)
                if (
                    changeset is not None
                    and changeset.status is ChangesetStatus.REJECTED
                    and command is not None
                    and command.command_name == "edit.apply_changeset"
                    and command.status in {CommandStatus.REJECTED, CommandStatus.CANCELLED}
                    and command.task_id == approval.task_id == changeset.task_id
                    and command.conversation_id == changeset.conversation_id
                    and approval.decision is not ApprovalDecision.PENDING
                ):
                    return None
                invocation = self._changeset_tool_invocation(unit_of_work, approval)
            return invocation.turn_id if invocation is not None else None
        with self._unit_of_work_factory() as unit_of_work:
            if approval.tool_invocation_id is not None:
                invocation = unit_of_work.assistant.get_tool_invocation(approval.tool_invocation_id)
                if (
                    invocation is None
                    or invocation.command_run_id != approval.command_run_id
                    or invocation.task_id != approval.task_id
                ):
                    raise InvalidTransitionError(
                        "approval does not match its Assistant Tool Invocation"
                    )
                turn = unit_of_work.assistant.get_turn(invocation.turn_id)
                if turn is None or turn.task_id != approval.task_id:
                    raise InvalidTransitionError(
                        "approval Tool Invocation does not match its Assistant Turn"
                    )
                return turn.id

            command = unit_of_work.commands.get_run(approval.command_run_id)
            if command is None:
                raise KeyError(f"command run not found: {approval.command_run_id}")
            if command.command_name != "model.generate.expensive":
                return None
            raw_turn_id = command.input_payload.get("turn_id")
            try:
                turn_id = UUID(str(raw_turn_id))
            except (TypeError, ValueError) as error:
                raise InvalidTransitionError(
                    "model budget approval has no valid Assistant Turn"
                ) from error
            turn = unit_of_work.assistant.get_turn(turn_id)
            if (
                turn is None
                or turn.task_id != approval.task_id
                or command.task_id != turn.task_id
                or command.conversation_id != turn.conversation_id
                or command.scope_digest != turn.scope_digest
            ):
                raise InvalidTransitionError(
                    "model budget approval does not match its Assistant Turn"
                )
            if turn.budget_approval_run_id != command.id:
                if (
                    command.status in {CommandStatus.CANCELLED, CommandStatus.REJECTED}
                    and approval.decision is not ApprovalDecision.PENDING
                ):
                    # Superseded, already-decided budget cards remain historical.
                    # Replaying their decision must not resume the revised route.
                    return None
                raise InvalidTransitionError("model budget approval binding has changed")
            return turn.id

    def _decide_approval(self, request: BaseModel) -> Any:
        validated = cast(ApprovalDecisionInput, request)
        approval = self._application.get_approval(validated.approval_id)
        was_pending = approval.decision is ApprovalDecision.PENDING
        assistant_turn_id = self._assistant_turn_id_for_approval(approval)
        changeset = None
        if approval.changeset_id is not None:
            try:
                changeset = self._application.decide_approval(
                    approval_id=validated.approval_id,
                    approved=validated.approved,
                    decided_by="user",
                )
            except Exception:
                self._resume_failed_changeset_owner(approval)
                raise
        else:
            self._application.record_approval_decision(
                approval_id=validated.approval_id,
                approved=validated.approved,
                decided_by="user",
            )
        decided = self._application.get_approval(validated.approval_id)
        if was_pending and changeset is not None and assistant_turn_id is None:
            # The proposal can be approved before its tool receipt is published.
            # Recheck after the apply transaction; receipt publication also reads
            # the Changeset under a lock, so neither completion order loses wakeup.
            assistant_turn_id = self._assistant_turn_id_for_approval(decided)
        if was_pending and assistant_turn_id is not None and changeset is not None:
            with self._unit_of_work_factory() as unit_of_work:
                invocation = self._changeset_tool_invocation(unit_of_work, decided)
            if invocation is None:
                raise InvalidTransitionError(
                    "Changeset approval lost its Assistant Tool Invocation"
                )
            applied = changeset.status is ChangesetStatus.APPLIED
            public_summary = (
                f"Applied {len(changeset.files)} Workspace file(s)"
                if applied
                else "Changeset was rejected"
            )
            self._assistant_application.resolve_external_tool_approval(
                turn_id=assistant_turn_id,
                invocation_id=invocation.id,
                approved=applied,
                public_summary=public_summary,
                model_content=json.dumps(
                    {
                        "changeset_id": str(changeset.id),
                        "approval_id": str(decided.id),
                        "status": changeset.status.value,
                        "files": list(changeset.files),
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                ),
            )
        if assistant_turn_id is not None:
            self._assistant_scheduler.start(
                assistant_turn_id,
                restart_if_running=was_pending,
            )
        return {
            "approval": decided,
            "changeset": changeset,
            "assistant_turn_id": assistant_turn_id,
            "resume_requested": assistant_turn_id is not None,
        }

    def _resume_failed_changeset_owner(self, original: Approval) -> None:
        with self._unit_of_work_factory() as unit:
            approval = unit.state.get_approval(original.id)
            changeset = unit.state.get_changeset(original.changeset_id)
            command = unit.commands.get_run(original.command_run_id)
            if (
                approval is None
                or approval.decision is not ApprovalDecision.APPROVED
                or changeset is None
                or changeset.status is not ChangesetStatus.FAILED
                or changeset.task_id != original.task_id
                or command is None
                or command.status is not CommandStatus.FAILED
                or command.command_name != "edit.apply_changeset"
                or command.task_id != changeset.task_id
                or command.conversation_id != changeset.conversation_id
            ):
                return
            invocation = self._changeset_tool_invocation(unit, approval)
        if invocation is not None:
            self._assistant_scheduler.start(invocation.turn_id, restart_if_running=True)

    @staticmethod
    def _changeset_tool_invocation(unit_of_work, approval: Approval):
        if approval.changeset_id is None:
            return None
        matches = []
        for turn in unit_of_work.assistant.nonterminal_turns_for_tasks((approval.task_id,)):
            for invocation in unit_of_work.assistant.list_tool_invocations(turn.id):
                if (
                    invocation.tool_name == "edit.propose_changeset"
                    and invocation.status is ToolInvocationStatus.COMPLETED
                    and tool_result_changeset_id(invocation.model_content) == approval.changeset_id
                ):
                    matches.append(invocation)
        if len(matches) > 1:
            raise InvalidTransitionError(
                "Changeset approval matches multiple Assistant Tool Invocations"
            )
        return matches[0] if matches else None
