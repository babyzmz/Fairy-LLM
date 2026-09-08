from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from fairy_core.assistant.candidates import ToolCandidate
from fairy_core.assistant.command_leases import assistant_command_lease_until
from fairy_core.assistant.durable_context import durable_tool_context
from fairy_core.assistant.models import (
    AssistantTurnStatus,
    Message,
    MessageRole,
    MessageVisibility,
    ToolInvocation,
    ToolInvocationStatus,
)
from fairy_core.assistant.tools import tool_message_content, uses_deferred_media
from fairy_core.assistant.turn_reader import require_task, require_turn
from fairy_core.commanding import CommandRun, CommandStatus, EventVisibility
from fairy_core.domain.errors import VersionConflictError
from fairy_core.domain.models import TaskStatus
from fairy_core.providers import ModelMessage, ModelToolCall


class AssistantToolContextMixin:
    def settle_cancelled_domain_tool(self, original: CommandRun) -> None:
        """Only the domain's confirmed local stop may close a yielded Tool Invocation."""
        with self._unit_of_work_factory() as unit:
            run = unit.commands.get_run(original.id)
            invocation = unit.assistant.find_tool_invocation_by_command_run_id(original.id)
            if (
                run is None or invocation is None or not uses_deferred_media(run)
                or invocation.task_id != original.task_id
                or invocation.scope_digest != original.scope_digest
                or run.scope_digest != original.scope_digest
            ):
                return
            turn = require_turn(unit, invocation.turn_id)
            if turn.status is not AssistantTurnStatus.CANCELLED:
                return
            if invocation.status is not ToolInvocationStatus.RUNNING:
                return
            if run.status is not CommandStatus.RUNNING:
                return
            if run.lease_until is None or run.lease_until > datetime.now(UTC):
                # A still-live parent owns its own cancellation acknowledgement.
                return
            claimed = self._command_bus(unit.commands).start(
                run.id, lease_until=assistant_command_lease_until(),
            )
            self._cancel_running_tool_in_unit(unit, invocation, claimed)
            unit.commit()

    def _durable_tool_context(self, turn_id: UUID) -> tuple[ModelMessage, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            invocations = unit_of_work.assistant.list_tool_invocations(turn_id)
        return durable_tool_context(invocations)

    def _changeset_approval_state(self, turn_id: UUID) -> str:
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            task = require_task(unit_of_work, turn.task_id)
        if task.status is TaskStatus.AWAITING_APPROVAL:
            return "waiting"
        if task.status is TaskStatus.REJECTED:
            return "rejected"
        return "ready"

    def _next_model_round(self, turn_id: UUID) -> int:
        with self._unit_of_work_factory() as unit_of_work:
            invocations = unit_of_work.assistant.list_tool_invocations(turn_id)
        return max((invocation.model_round for invocation in invocations), default=0) + 1

    @staticmethod
    def _model_tool_call(candidate: ToolCandidate) -> ModelToolCall:
        arguments = candidate.arguments()
        return ModelToolCall.create(
            tool_call_id=candidate.call_id,
            name=candidate.name or "unknown",
            arguments=json.dumps(
                arguments,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    def _append_tool_message(
        self,
        *,
        turn_id: UUID,
        tool_name: str,
        tool_call_id: str,
        content: str,
        rejected: bool,
    ) -> Message:
        with self._unit_of_work_factory() as unit_of_work:
            message = self._append_tool_message_in_unit(
                unit_of_work,
                turn_id=turn_id,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                content=content,
                rejected=rejected,
            )
            unit_of_work.commit()
        return message

    @staticmethod
    def _append_tool_message_in_unit(
        unit_of_work,
        *,
        turn_id: UUID,
        tool_name: str,
        tool_call_id: str,
        content: str,
        rejected: bool,
    ) -> Message:
        turn = require_turn(unit_of_work, turn_id)
        message = Message.create(
            conversation_id=turn.conversation_id,
            task_id=turn.task_id,
            turn_id=turn.id,
            sequence=unit_of_work.assistant.next_message_sequence(turn.conversation_id),
            role=MessageRole.TOOL,
            visibility=MessageVisibility.DEVELOPER,
            content=tool_message_content(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                content=content,
                rejected=rejected,
            ),
        )
        unit_of_work.assistant.append_message(message)
        return message

    def _cancel_running_tool(
        self,
        invocation: ToolInvocation,
        run: CommandRun,
    ) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            self._cancel_running_tool_in_unit(unit_of_work, invocation, run)
            unit_of_work.commit()

    def _abandon_running_tool(self, run: CommandRun) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            abandoned = unit_of_work.commands.abandon(
                run.id,
                lease_owner=run.lease_owner or "",
                lease_fence=run.lease_fence,
            )
            if abandoned:
                unit_of_work.commit()

    def _cancel_running_tool_in_unit(
        self,
        unit_of_work,
        invocation: ToolInvocation,
        run: CommandRun,
    ) -> None:
        if invocation.status is ToolInvocationStatus.RUNNING:
            expected_status = invocation.status
            invocation.cancel()
            unit_of_work.assistant.update_tool_invocation(
                invocation,
                expected_status=expected_status,
            )
        self._tool_trace.cancel_in_unit(unit_of_work, run=run)
        persisted_run = unit_of_work.commands.get_run(run.id)
        if persisted_run is not None and persisted_run.status is CommandStatus.RUNNING:
            unit_of_work.commands.append_event(
                run_id=run.id,
                event_type="assistant.turn.cancelled",
                visibility=EventVisibility.USER,
                message="Assistant turn cancelled",
                payload={"turn_id": str(invocation.turn_id)},
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
            )
            self._command_bus(unit_of_work.commands).cancel(
                run.id,
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
            )

    def _resume_after_tools(self, turn_id: UUID) -> bool:
        try:
            with self._unit_of_work_factory() as unit_of_work:
                turn = require_turn(unit_of_work, turn_id)
                if turn.status is AssistantTurnStatus.CANCELLED:
                    return False
                expected_status = turn.status
                expected_revision = turn.cancellation_revision
                turn.resume()
                unit_of_work.assistant.update_turn(
                    turn,
                    expected_status=expected_status,
                    expected_cancellation_revision=expected_revision,
                )
                unit_of_work.commit()
            return True
        except VersionConflictError:
            with self._unit_of_work_factory() as unit_of_work:
                persisted = require_turn(unit_of_work, turn_id)
            if persisted.status is AssistantTurnStatus.CANCELLED:
                return False
            raise
