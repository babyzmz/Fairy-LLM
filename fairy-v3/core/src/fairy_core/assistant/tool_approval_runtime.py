from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fairy_core.assistant.models import AssistantTurnStatus, ToolInvocationStatus
from fairy_core.assistant.turn_reader import require_task, require_turn
from fairy_core.assistant.work_queue import assistant_command_lease_until
from fairy_core.commanding import CommandStatus
from fairy_core.providers import CancellationToken


class AssistantToolApprovalMixin:
    """Resumes approval-gated tools while preserving their original command identity."""

    _unit_of_work_factory: Any
    _registry: Any
    _scope_resolver: Any
    _tool_trace: Any

    def resolve_external_tool_approval(
        self,
        *,
        turn_id: UUID,
        invocation_id: UUID,
        approved: bool,
        public_summary: str,
        model_content: str,
    ) -> None:
        """Make a deferred Changeset decision visible to the original model/tool chain."""
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            invocation = unit_of_work.assistant.get_tool_invocation(invocation_id)
            if invocation is None or invocation.turn_id != turn.id:
                raise ValueError("Tool Invocation does not belong to the Assistant Turn")
            if invocation.command_run_id is None:
                raise ValueError("Tool Invocation has no CommandRun")
            run = unit_of_work.commands.get_run(invocation.command_run_id)
            if run is None:
                raise ValueError("Tool Invocation CommandRun is unavailable")
            expected_status = invocation.status
            invocation.revise_completed_result(
                public_summary=public_summary,
                model_content=model_content,
            )
            unit_of_work.assistant.update_tool_invocation(
                invocation,
                expected_status=expected_status,
            )
            if approved:
                self._tool_trace.approve_in_unit(unit_of_work, run=run)
                self._tool_trace.complete_in_unit(
                    unit_of_work,
                    turn=turn,
                    run=run,
                    public_summary=public_summary,
                    artifact_refs=(),
                )
            else:
                self._tool_trace.reject_in_unit(unit_of_work, run=run)
            unit_of_work.commit()

    def _resume_pending_tool(
        self,
        turn_id: UUID,
        cancellation: CancellationToken,
    ) -> bool:
        cancellation.raise_if_cancelled()
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            if turn.status is not AssistantTurnStatus.WAITING_FOR_TOOL:
                raise ValueError("Assistant Turn is not waiting for a tool")
            pending = [
                invocation
                for invocation in unit_of_work.assistant.list_tool_invocations(turn_id)
                if invocation.status in {ToolInvocationStatus.QUEUED, ToolInvocationStatus.RUNNING}
            ]
            if not pending:
                return False
            if len(pending) != 1:
                raise RuntimeError("Assistant Turn has multiple pending Tool Invocations")
            invocation = pending[0]
            if invocation.command_run_id is None:
                raise RuntimeError("pending Tool Invocation has no CommandRun")
            command = unit_of_work.commands.get_run(invocation.command_run_id)
            if command is None:
                raise RuntimeError("pending Tool Invocation CommandRun is missing")
            definition = self._registry.get(invocation.tool_name)
            if definition is None:
                raise RuntimeError("pending Tool Invocation definition is missing")
            expected_definition_digest = command.input_payload.get("definition_digest")
            if (
                definition.source != "builtin"
                and expected_definition_digest != definition.definition_digest
            ):
                expected_status = invocation.status
                invocation.fail(error_code="MCP_SCHEMA_CHANGED")
                unit_of_work.assistant.update_tool_invocation(
                    invocation,
                    expected_status=expected_status,
                )
                self._append_tool_message_in_unit(
                    unit_of_work,
                    turn_id=turn_id,
                    tool_name=invocation.tool_name,
                    tool_call_id=invocation.provider_call_id,
                    content="Tool schema changed before the approved call could run.",
                    rejected=True,
                )
                if command.status is CommandStatus.QUEUED:
                    unit_of_work.commands.transition(
                        command.id,
                        CommandStatus.INTERRUPTED,
                    )
                elif command.status is CommandStatus.RUNNING:
                    unit_of_work.commands.transition(
                        command.id,
                        CommandStatus.INTERRUPTED,
                        lease_owner=command.lease_owner,
                        lease_fence=command.lease_fence,
                    )
                self._tool_trace.fail_in_unit(
                    unit_of_work,
                    run=command,
                    error_code="MCP_SCHEMA_CHANGED",
                )
                unit_of_work.commit()
                return False
            task = require_task(unit_of_work, turn.task_id)
            scope = self._scope_resolver(unit_of_work.state, task)

            if command.status is CommandStatus.WAITING_APPROVAL:
                return True
            if command.status is CommandStatus.REJECTED:
                expected_status = invocation.status
                invocation.reject(
                    error_code="USER_REJECTED",
                    model_content="The user rejected this tool execution.",
                )
                unit_of_work.assistant.update_tool_invocation(
                    invocation,
                    expected_status=expected_status,
                )
                self._append_tool_message_in_unit(
                    unit_of_work,
                    turn_id=turn_id,
                    tool_name=invocation.tool_name,
                    tool_call_id=invocation.provider_call_id,
                    content=invocation.model_content or "The user rejected this tool execution.",
                    rejected=True,
                )
                self._tool_trace.reject_in_unit(unit_of_work, run=command)
                unit_of_work.commit()
                return False
            bus = self._command_bus(unit_of_work.commands)
            if command.status is CommandStatus.QUEUED:
                running = bus.start(
                    command.id,
                    lease_until=assistant_command_lease_until(),
                )
            elif command.status is CommandStatus.RUNNING:
                if command.lease_until is None or command.lease_until > datetime.now(UTC):
                    return True
                running = bus.start(
                    command.id,
                    lease_until=assistant_command_lease_until(),
                )
            else:
                expected_status = invocation.status
                invocation.reject(
                    error_code=(
                        "WORKER_INTERRUPTED"
                        if command.status is CommandStatus.INTERRUPTED
                        else "TOOL_REJECTED"
                    ),
                    model_content=(
                        f"Tool execution ended before completion ({command.status.value})."
                    ),
                )
                unit_of_work.assistant.update_tool_invocation(
                    invocation,
                    expected_status=expected_status,
                )
                self._append_tool_message_in_unit(
                    unit_of_work,
                    turn_id=turn_id,
                    tool_name=invocation.tool_name,
                    tool_call_id=invocation.provider_call_id,
                    content=invocation.model_content or "Tool execution did not complete.",
                    rejected=True,
                )
                self._tool_trace.fail_in_unit(
                    unit_of_work,
                    run=command,
                    error_code=invocation.error_code or "TOOL_REJECTED",
                )
                unit_of_work.commit()
                return False

            self._tool_trace.approve_in_unit(unit_of_work, run=running)
            if invocation.status is ToolInvocationStatus.QUEUED:
                expected_status = invocation.status
                invocation.start()
                unit_of_work.assistant.update_tool_invocation(
                    invocation,
                    expected_status=expected_status,
                )
            unit_of_work.commit()

        self._execute_running_tool(
            turn_id=turn_id,
            invocation=invocation,
            running=running,
            definition=definition,
            scope=scope,
            arguments=invocation.arguments,
            cancellation=cancellation,
        )
        return False


__all__ = ["AssistantToolApprovalMixin"]
