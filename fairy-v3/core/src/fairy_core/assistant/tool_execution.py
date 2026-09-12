from __future__ import annotations

from uuid import UUID

from fairy_core.assistant.changeset_receipts import reconcile_changeset_receipt
from fairy_core.assistant.evidence import (
    evidence_context,
    seal_evidence_drafts,
)
from fairy_core.assistant.execution_intent_policy import (
    ExecutionIntentError,
    file_target_issue,
    readonly_intent_issue,
)
from fairy_core.assistant.models import (
    AssistantTurnStatus,
    Message,
    ToolInvocation,
)
from fairy_core.assistant.tools import (
    ToolExecutionDeferred,
    ToolResumeResult,
)
from fairy_core.assistant.turn_reader import require_turn
from fairy_core.commanding import CommandRun
from fairy_core.mcp.ports import McpCancelledError
from fairy_core.providers import (
    CancellationToken,
    ModelImage,
    ProviderAuthenticationError,
    ProviderCancelledError,
    ProviderContentRejectedError,
    ProviderContextLengthError,
    ProviderError,
    ProviderNetworkError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class AssistantToolExecutionMixin:
    def _execute_running_tool(
        self,
        *,
        turn_id: UUID,
        invocation: ToolInvocation,
        running: CommandRun,
        definition,
        scope,
        arguments: dict[str, object],
        cancellation: CancellationToken,
        reconciled: ToolResumeResult | None = None,
    ) -> tuple[Message, bool, str | None, tuple[ModelImage, ...]]:
        try:
            cancellation.raise_if_cancelled()
            self._turns.require_waiting_for_tool(turn_id)
            with self._unit_of_work_factory() as unit_of_work:
                intent = unit_of_work.assistant.get_execution_intent(turn_id)
                # A known domain receipt records an already-dispatched outcome;
                # steering may narrow future authority but cannot erase that fact.
                # Reconciliation still validates the current scope below, and the
                # domain reader validates the original Command/Job/Run binding.
                intent_issue = (
                    readonly_intent_issue(intent, definition) if reconciled is None else None
                )
                if intent is not None and (
                    intent.turn_id != turn_id
                    or intent.task_id != scope.task_id
                    or intent.conversation_id != scope.conversation_id
                    or intent.workspace_id != scope.workspace_id
                    or intent.project_id != scope.project_id
                    or intent.execution_target != scope.execution_target
                    or intent.base_version_id != scope.base_version_id
                    or intent.target_version_id != scope.target_version_id
                ):
                    intent_issue = "EXECUTION_INTENT_SCOPE_CHANGED"
                if (
                    reconciled is None
                    and intent is not None
                    and definition.name == "edit.propose_changeset"
                ):
                    intent_issue = intent_issue or file_target_issue(intent, arguments.get("files"))
                expected_revision = running.input_payload.get("interpretation_revision")
                if (
                    reconciled is None
                    and expected_revision is not None
                    and (intent is None or intent.interpretation_revision != expected_revision)
                ):
                    intent_issue = "EXECUTION_INTENT_CHANGED"
                if intent_issue is not None:
                    raise ExecutionIntentError(intent_issue)
            if definition.name == "preview.status" and self._execution_completion_hook is not None:
                # Preview is Core-owned. Prepare and verify the durable target version before the
                # model reads status so it cannot spin on a transient null projection.
                self._execution_completion_hook(turn_id)
                cancellation.raise_if_cancelled()
                self._turns.require_waiting_for_tool(turn_id)
            current_definition = self._registry.get(definition.name)
            if (
                current_definition is None
                or current_definition.definition_digest != definition.definition_digest
            ):
                error = RuntimeError("tool definition changed before execution")
                error.error_code = "MCP_SCHEMA_CHANGED"  # type: ignore[attr-defined]
                raise error
            execute_with_cancellation = getattr(
                self._tool_executor,
                "execute_command_with_cancellation",
                None,
            )
            if reconciled is not None:
                if reconciled.error_code is not None:
                    error = RuntimeError(reconciled.error_code)
                    error.error_code = reconciled.error_code
                    raise error
                if reconciled.result is None:
                    raise ToolExecutionDeferred
                result = reconciled.result
            elif callable(execute_with_cancellation):
                result = execute_with_cancellation(
                    definition,
                    scope,
                    arguments,
                    command_run=running,
                    cancellation=cancellation,
                )
            else:
                execute_command = getattr(self._tool_executor, "execute_command", None)
                if callable(execute_command):
                    result = execute_command(
                        definition,
                        scope,
                        arguments,
                        command_run=running,
                    )
                else:
                    result = self._tool_executor.execute(definition, scope, arguments)
            cancellation.raise_if_cancelled()
            self._turns.require_waiting_for_tool(turn_id)
        except ToolExecutionDeferred:
            raise
        except (ProviderCancelledError, McpCancelledError):
            if cancellation.is_interrupted:
                self._abandon_running_tool(running)
            else:
                self._cancel_running_tool(invocation, running)
            raise
        except Exception as error:
            error_code = _tool_error_code(error)
            model_detail = getattr(error, "model_detail", None)
            failure_content = f"Tool execution failed ({error_code})."
            if isinstance(model_detail, str) and model_detail:
                failure_content = f"{failure_content} Recovery: {model_detail}"
            with self._unit_of_work_factory() as unit_of_work:
                expected_status = invocation.status
                invocation.fail(error_code=error_code)
                invocation.model_content = failure_content
                unit_of_work.assistant.update_tool_invocation(
                    invocation,
                    expected_status=expected_status,
                )
                self._tool_trace.fail_in_unit(
                    unit_of_work,
                    run=running,
                    error_code=error_code,
                )
                self._command_bus(unit_of_work.commands).fail(
                    running.id,
                    error_code=error_code,
                    lease_owner=running.lease_owner,
                    lease_fence=running.lease_fence,
                )
                message = self._append_tool_message_in_unit(
                    unit_of_work,
                    turn_id=turn_id,
                    tool_name=definition.name,
                    tool_call_id=invocation.provider_call_id,
                    content=failure_content,
                    rejected=True,
                )
                unit_of_work.commit()
            return message, False, error_code, ()

        with self._unit_of_work_factory() as unit_of_work:
            persisted_turn = require_turn(unit_of_work, turn_id)
            if (
                cancellation.is_cancelled
                or persisted_turn.status is not AssistantTurnStatus.WAITING_FOR_TOOL
            ):
                self._cancel_running_tool_in_unit(unit_of_work, invocation, running)
                unit_of_work.commit()
                raise ProviderCancelledError("Assistant Turn is no longer active")
            changeset_status = None
            if definition.name == "edit.propose_changeset" and definition.source == "builtin":
                result, changeset_status = reconcile_changeset_receipt(
                    unit_of_work,
                    persisted_turn,
                    result,
                )
            expected_status = invocation.status
            evidence_receipts = seal_evidence_drafts(
                result.evidence_drafts,
                invocation_id=invocation.id,
                turn_id=persisted_turn.id,
                tool_name=definition.name,
                scope=scope,
            )
            invocation.complete(
                public_summary=result.public_summary,
                model_content=result.model_content,
                artifact_ids=result.artifact_ids,
                evidence_receipts=evidence_receipts,
            )
            unit_of_work.assistant.update_tool_invocation(
                invocation,
                expected_status=expected_status,
            )
            if changeset_status == "rejected":
                self._tool_trace.reject_in_unit(unit_of_work, run=running)
            elif changeset_status == "failed":
                self._tool_trace.approve_in_unit(unit_of_work, run=running)
                self._tool_trace.fail_in_unit(
                    unit_of_work,
                    run=running,
                    error_code="CHANGESET_APPLY_FAILED",
                )
            elif result.awaiting_approval:
                self._tool_trace.wait_for_external_approval_in_unit(
                    unit_of_work,
                    turn=persisted_turn,
                    run=running,
                    public_summary=result.public_summary,
                )
            else:
                self._tool_trace.complete_in_unit(
                    unit_of_work,
                    turn=persisted_turn,
                    run=running,
                    public_summary=result.public_summary,
                    artifact_refs=result.artifact_ids,
                )
            self._command_bus(unit_of_work.commands).complete(
                running.id,
                output={
                    "public_summary": result.public_summary,
                    "model_content": result.model_content,
                    "artifact_ids": [str(value) for value in result.artifact_ids],
                    "evidence_receipt_ids": [str(value.id) for value in evidence_receipts],
                },
                lease_owner=running.lease_owner,
                lease_fence=running.lease_fence,
            )
            message = self._append_tool_message_in_unit(
                unit_of_work,
                turn_id=turn_id,
                tool_name=definition.name,
                tool_call_id=invocation.provider_call_id,
                content=result.model_content + evidence_context(evidence_receipts),
                rejected=False,
            )
            unit_of_work.commit()
        return message, result.awaiting_approval, None, result.images


def _tool_error_code(error: Exception) -> str:
    if isinstance(error, ProviderUnavailableError):
        return error.public_code
    explicit = getattr(error, "error_code", getattr(error, "code", None))
    if isinstance(explicit, str):
        normalized = explicit.strip().upper()
        if (
            normalized
            and len(normalized) <= 128
            and all(character.isalnum() or character == "_" for character in normalized)
        ):
            return normalized
    categories: tuple[tuple[type[Exception], str], ...] = (
        (ProviderAuthenticationError, "PROVIDER_AUTHENTICATION_FAILED"),
        (ProviderRateLimitError, "PROVIDER_RATE_LIMITED"),
        (ProviderTimeoutError, "PROVIDER_TIMEOUT"),
        (ProviderProtocolError, "PROVIDER_PROTOCOL_ERROR"),
        (ProviderContextLengthError, "PROVIDER_CONTEXT_LENGTH_EXCEEDED"),
        (ProviderContentRejectedError, "PROVIDER_CONTENT_REJECTED"),
        (ProviderNetworkError, "PROVIDER_NETWORK_ERROR"),
        (ProviderUnavailableError, "PROVIDER_UNAVAILABLE"),
        (ProviderError, "PROVIDER_ERROR"),
    )
    for error_type, error_code in categories:
        if isinstance(error, error_type):
            return error_code
    return "TOOL_EXECUTION_FAILED"
