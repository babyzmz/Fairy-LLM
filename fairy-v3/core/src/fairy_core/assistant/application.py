from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from fairy_core.assistant import limits
from fairy_core.assistant.candidates import ToolCandidate, arguments_for_definition
from fairy_core.assistant.context import AssistantContextBuilder
from fairy_core.assistant.durable_context import durable_tool_context
from fairy_core.assistant.media_routing import constrain_context_for_media
from fairy_core.assistant.models import (
    AssistantTurn,
    AssistantTurnStatus,
    Message,
    MessageRole,
    MessageVisibility,
    ToolInvocation,
    ToolInvocationStatus,
)
from fairy_core.assistant.plan_budget import consume_tool_budget
from fairy_core.assistant.provider_attempts import ProviderAttemptRecorder
from fairy_core.assistant.routing_runtime import AssistantRoutingMixin
from fairy_core.assistant.tool_trace import ToolTraceCoordinator
from fairy_core.assistant.tools import (
    DIRECT_ANSWER_TOOL_NAME,
    ToolCandidateError,
    ToolExecutor,
    UnavailableToolExecutor,
    direct_answer,
    sanitize_public_intent,
    tool_message_content,
)
from fairy_core.assistant.trace_runtime import TurnTraceRuntime
from fairy_core.assistant.turn_lifecycle import AssistantTurnLifecycleMixin
from fairy_core.assistant.turn_reader import AssistantTurnReader, require_task, require_turn
from fairy_core.assistant.work_queue import assistant_command_lease_until
from fairy_core.commanding import CommandRun, CommandStatus, EventVisibility
from fairy_core.commanding.bus import CommandRequest
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.commanding.settings import ExecutionPolicyResolver
from fairy_core.domain.execution import Approval
from fairy_core.domain.models import TaskStatus
from fairy_core.mcp.ports import McpCancelledError
from fairy_core.model_catalog.models import (
    ModelSelectionMode,
)
from fairy_core.perception import ImageAttachmentStore
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import (
    CancellationToken,
    ModelDeltaKind,
    ModelExecutionRole,
    ModelMessage,
    ModelRequest,
    ModelRole,
    ModelToolCall,
    ProviderAuthenticationError,
    ProviderCancelledError,
    ProviderContentRejectedError,
    ProviderContextLengthError,
    ProviderError,
    ProviderNetworkError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderRegistry,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class AssistantApplication(AssistantRoutingMixin, AssistantTurnLifecycleMixin):
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        scope_resolver,
        registry: ToolRegistry,
        providers: ProviderRegistry,
        image_attachments: ImageAttachmentStore,
        tool_executor: ToolExecutor | None = None,
        execution_policy: ExecutionPolicyResolver | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._scope_resolver = scope_resolver
        self._registry = registry
        self._policy = PolicyEngine(registry)
        self._providers = providers
        self._image_attachments = image_attachments
        self._tool_executor = tool_executor or UnavailableToolExecutor()
        self._execution_policy = execution_policy or ExecutionPolicyResolver()
        self._trace = TurnTraceRuntime(unit_of_work_factory)
        self._tool_trace = ToolTraceCoordinator(self._trace)
        self._provider_attempts = ProviderAttemptRecorder(
            unit_of_work_factory,
            trace_runtime=self._trace,
        )
        self._turns = AssistantTurnReader(unit_of_work_factory)
        self._context = AssistantContextBuilder(
            unit_of_work_factory=unit_of_work_factory,
            registry=registry,
            scope_resolver=scope_resolver,
            image_attachments=image_attachments,
            execution_policy=self._execution_policy,
        )

    def run_turn(
        self,
        turn_id: UUID,
        cancellation: CancellationToken,
    ) -> AssistantTurn:
        turn = self._turns.get(turn_id)
        if turn.is_terminal:
            self._image_attachments.release(turn_id)
            return turn
        all_text: list[str] = []
        usage: dict[str, int] = {}
        tool_count = self._turns.tool_count(turn_id)
        current_run: CommandRun | None = None
        chunk_index = 0
        model_round_start = 1
        ephemeral_context: list[ModelMessage] = []
        try:
            if turn.status is AssistantTurnStatus.WAITING_FOR_TOOL:
                if turn.budget_approval_run_id is not None:
                    budget_state = self._resume_budget_approval(turn_id, cancellation)
                    if budget_state == "waiting":
                        return self._turns.get(turn_id)
                    if budget_state == "rejected":
                        return self._fail_turn(
                            turn_id,
                            None,
                            error_code="USER_REJECTED",
                        )
                else:
                    if self._resume_pending_tool(turn_id, cancellation):
                        return self._turns.get(turn_id)
                    self._resume_after_tools(turn_id)
                    ephemeral_context.extend(self._durable_tool_context(turn_id))
                    model_round_start = self._next_model_round(turn_id)

            turn = self._turns.get(turn_id)
            decision = self._ensure_routing(turn, cancellation)
            turn = self._turns.get(turn_id)
            if decision is not None and decision.approval_required:
                if turn.budget_approval_run_id is None:
                    return self._request_budget_approval(turn_id, decision)
                if turn.status is AssistantTurnStatus.WAITING_FOR_TOOL:
                    return turn
            if (
                turn.model_selection is not None
                and turn.model_selection.mode is ModelSelectionMode.AUTO
                and model_round_start == 1
            ):
                model_round_start = 2

            final_model_round = limits.MAX_MODEL_ROUNDS
            if decision is not None and decision.reviewer_model_id is not None:
                final_model_round -= 1
            for model_round in range(model_round_start, final_model_round + 1):
                cancellation.raise_if_cancelled()
                profile = self._execution_profile(turn, decision)
                turn, current_run = self._start_model_round(
                    turn_id,
                    model_round,
                    profile_id=profile.id,
                    model_role=ModelExecutionRole.PRIMARY,
                )
                context = self._context.build(
                    turn,
                    provider_capabilities=profile.capabilities,
                )
                context = constrain_context_for_media(context, decision)
                request = ModelRequest.create(
                    profile_id=profile.id,
                    messages=(*context.messages, *ephemeral_context),
                    tools=context.tools,
                    required_capabilities=context.required_capabilities,
                    max_output_tokens=(
                        decision.estimated_output_tokens if decision is not None else 16_384
                    ),
                    model_role=ModelExecutionRole.PRIMARY,
                    fallback_profile_ids=self._fallback_profile_ids(
                        turn=turn,
                        decision=decision,
                        required_capabilities=context.required_capabilities,
                    ),
                    allow_profile_fallback=turn.model_selection is None,
                    require_parameters=turn.model_selection is not None,
                    deny_data_collection=True,
                    zero_data_retention=(
                        turn.model_selection.zero_data_retention
                        if turn.model_selection is not None
                        else False
                    ),
                )
                offered_definitions = {
                    definition.name: definition for definition in context.tool_definitions
                }
                candidates: dict[str, ToolCandidate] = {}
                round_text: list[str] = []
                for delta in self._providers.stream(
                    request,
                    cancellation,
                    on_attempt=self._provider_attempts.observer(
                        turn_id,
                        model_round,
                        run=current_run,
                    ),
                ):
                    cancellation.raise_if_cancelled()
                    self._turns.require_active(turn_id)
                    if delta.kind is ModelDeltaKind.TEXT:
                        assert delta.text is not None
                        visible_length = sum(map(len, all_text))
                        buffered_length = (
                            sum(map(len, round_text))
                            if decision is not None and decision.reviewer_model_id is not None
                            else 0
                        )
                        if (
                            visible_length + buffered_length + len(delta.text)
                            > limits.MAX_ASSISTANT_CHARACTERS
                        ):
                            raise ValueError("assistant output exceeds the durable message limit")
                        round_text.append(delta.text)
                        if decision is None or decision.reviewer_model_id is None:
                            chunk_index += 1
                            self._append_delta(
                                turn_id=turn_id,
                                run=current_run,
                                model_round=model_round,
                                chunk_index=chunk_index,
                                text=delta.text,
                            )
                            all_text.append(delta.text)
                    elif delta.kind is ModelDeltaKind.TOOL_CALL:
                        if delta.tool_call_id is None:
                            raise ToolCandidateError("tool candidate has no call id")
                        candidate = candidates.setdefault(
                            delta.tool_call_id,
                            ToolCandidate(call_id=delta.tool_call_id),
                        )
                        candidate.append(delta)
                    elif delta.kind is ModelDeltaKind.USAGE:
                        for name, value in delta.usage.items():
                            usage[name] = usage.get(name, 0) + value

                cancellation.raise_if_cancelled()
                direct_candidates = [
                    candidate
                    for candidate in candidates.values()
                    if candidate.name == DIRECT_ANSWER_TOOL_NAME
                ]
                external_candidates = [
                    candidate
                    for candidate in candidates.values()
                    if candidate.name != DIRECT_ANSWER_TOOL_NAME
                ]
                if len(direct_candidates) == 1 and not external_candidates:
                    answer = direct_answer(direct_candidates[0].arguments())
                    if decision is not None and decision.reviewer_model_id is not None:
                        self._complete_model_round(
                            current_run,
                            output={"draft_ready": True},
                        )
                        return self._review_and_complete(
                            turn_id=turn_id,
                            decision=decision,
                            source_messages=request.messages,
                            draft=answer,
                            model_round=model_round + 1,
                            chunk_index=chunk_index,
                            usage=usage,
                            cancellation=cancellation,
                        )
                    chunk_index += 1
                    self._append_delta(
                        turn_id=turn_id,
                        run=current_run,
                        model_round=model_round,
                        chunk_index=chunk_index,
                        text=answer,
                    )
                    all_text.append(answer)
                    return self._complete_turn(
                        turn_id=turn_id,
                        run=current_run,
                        content="".join(all_text),
                        usage=usage,
                    )
                if direct_candidates:
                    external_candidates = list(candidates.values())
                if external_candidates:
                    if turn.model_selection is not None and round_text:
                        self._reset_message_projection(
                            turn_id=turn_id,
                            run=current_run,
                            through_chunk_index=chunk_index,
                        )
                        return self._fail_turn(
                            turn_id,
                            current_run,
                            error_code="PROVIDER_PROTOCOL_ERROR",
                        )
                    if tool_count + len(external_candidates) > limits.MAX_TOOL_INVOCATIONS:
                        return self._fail_turn(
                            turn_id,
                            current_run,
                            error_code="ASSISTANT_TOOL_LIMIT",
                        )
                    self._wait_for_tools(
                        turn_id=turn_id,
                        run=current_run,
                        candidate_count=len(external_candidates),
                    )
                    ephemeral_context.append(
                        ModelMessage.create(
                            role=ModelRole.ASSISTANT,
                            content="".join(round_text),
                            tool_calls=tuple(
                                self._model_tool_call(candidate)
                                for candidate in external_candidates
                            ),
                        )
                    )
                    for candidate in external_candidates:
                        tool_count += 1
                        waiting, tool_message = self._execute_candidate(
                            turn_id=turn_id,
                            candidate=candidate,
                            model_round=model_round,
                            sequence=tool_count,
                            cancellation=cancellation,
                            offered_definitions=offered_definitions,
                        )
                        if waiting:
                            return self._turns.get(turn_id)
                        assert tool_message is not None
                        ephemeral_context.append(
                            ModelMessage.create(
                                role=ModelRole.TOOL,
                                content=tool_message.content,
                                name=candidate.name or "unknown",
                                tool_call_id=candidate.call_id,
                            )
                        )
                    self._resume_after_tools(turn_id)
                    current_run = None
                    continue
                if round_text:
                    if decision is not None and decision.reviewer_model_id is not None:
                        self._complete_model_round(
                            current_run,
                            output={"draft_ready": True},
                        )
                        return self._review_and_complete(
                            turn_id=turn_id,
                            decision=decision,
                            source_messages=request.messages,
                            draft="".join(round_text),
                            model_round=model_round + 1,
                            chunk_index=chunk_index,
                            usage=usage,
                            cancellation=cancellation,
                        )
                    return self._complete_turn(
                        turn_id=turn_id,
                        run=current_run,
                        content="".join(all_text),
                        usage=usage,
                    )
                return self._fail_turn(
                    turn_id,
                    current_run,
                    error_code="PROVIDER_PROTOCOL_ERROR",
                )
            return self._fail_turn(
                turn_id,
                current_run,
                error_code="ASSISTANT_ROUND_LIMIT",
            )
        except (ProviderCancelledError, McpCancelledError):
            if cancellation.is_interrupted:
                if current_run is not None:
                    self._abandon_model_run(current_run)
                raise
            return self._cancel_turn(turn_id, current_run)
        except ProviderAuthenticationError:
            return self._fail_turn(
                turn_id,
                current_run,
                error_code="PROVIDER_AUTHENTICATION_FAILED",
            )
        except ProviderRateLimitError:
            return self._fail_turn(
                turn_id,
                current_run,
                error_code="PROVIDER_RATE_LIMITED",
            )
        except ProviderTimeoutError:
            return self._fail_turn(
                turn_id,
                current_run,
                error_code="PROVIDER_TIMEOUT",
            )
        except ProviderProtocolError:
            return self._fail_turn(
                turn_id,
                current_run,
                error_code="PROVIDER_PROTOCOL_ERROR",
            )
        except ProviderContextLengthError:
            return self._fail_turn(
                turn_id,
                current_run,
                error_code="PROVIDER_CONTEXT_LENGTH_EXCEEDED",
            )
        except ProviderContentRejectedError:
            return self._fail_turn(
                turn_id,
                current_run,
                error_code="PROVIDER_CONTENT_REJECTED",
            )
        except ProviderNetworkError:
            return self._fail_turn(
                turn_id,
                current_run,
                error_code="PROVIDER_NETWORK_ERROR",
            )
        except ProviderUnavailableError:
            return self._fail_turn(
                turn_id,
                current_run,
                error_code="PROVIDER_UNAVAILABLE",
            )
        except ProviderError:
            return self._fail_turn(
                turn_id,
                current_run,
                error_code="PROVIDER_ERROR",
            )
        except (ToolCandidateError, ValueError):
            return self._fail_turn(
                turn_id,
                current_run,
                error_code="PROVIDER_PROTOCOL_ERROR",
            )
        except Exception:
            self._fail_turn(
                turn_id,
                current_run,
                error_code="ASSISTANT_INTERNAL_ERROR",
            )
            raise
        finally:
            self._turns.release_terminal_images(turn_id, self._image_attachments)

    def _execute_candidate(
        self,
        *,
        turn_id: UUID,
        candidate: ToolCandidate,
        model_round: int,
        sequence: int,
        cancellation: CancellationToken,
        offered_definitions: dict[str, object],
    ) -> tuple[bool, Message | None]:
        cancellation.raise_if_cancelled()
        self._turns.require_waiting_for_tool(turn_id)
        if candidate.name is None:
            message = self._append_tool_message(
                turn_id=turn_id,
                tool_name="unknown",
                tool_call_id=candidate.call_id,
                content="Tool candidate was rejected because its name is missing.",
                rejected=True,
            )
            return False, message
        definition = offered_definitions.get(candidate.name)
        if definition is None or not getattr(definition, "model_visible", False):
            message = self._append_tool_message(
                turn_id=turn_id,
                tool_name=candidate.name,
                tool_call_id=candidate.call_id,
                content="Tool candidate was rejected because it is not registered.",
                rejected=True,
            )
            return False, message
        public_intent = candidate.public_intent() or sanitize_public_intent(
            f"Use {definition.description}"
        )
        public_intent = public_intent or "Use a workspace tool"
        tool_summary = sanitize_public_intent(definition.description) or "Workspace tool"
        try:
            arguments = arguments_for_definition(candidate, definition)
        except ToolCandidateError as error:
            message = self._append_tool_message(
                turn_id=turn_id,
                tool_name=candidate.name,
                tool_call_id=candidate.call_id,
                content=str(error),
                rejected=True,
            )
            return False, message

        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            task = require_task(unit_of_work, turn.task_id)
            scope = self._scope_resolver(unit_of_work.state, task)
            invocation = ToolInvocation.create(
                turn=turn,
                model_round=model_round,
                sequence=sequence,
                provider_call_id=candidate.call_id,
                tool_name=definition.name,
                scope_digest=scope.scope_digest,
                arguments=arguments,
            )
            existing = unit_of_work.assistant.list_tool_invocations(turn_id)
            if any(item.argument_hash == invocation.argument_hash for item in existing):
                duplicate = True
                running = None
            else:
                duplicate = False
                if definition.name != "execution.plan":
                    consume_tool_budget(unit_of_work, task.id)
                current_definition = self._registry.get(definition.name)
                if (
                    current_definition is None
                    or current_definition.definition_digest != definition.definition_digest
                ):
                    invocation.reject(error_code="MCP_SCHEMA_CHANGED")
                    unit_of_work.assistant.save_tool_invocation(invocation)
                    unit_of_work.commit()
                    running = None
                    duplicate = False
                    definition_changed = True
                else:
                    definition_changed = False
                bus = self._command_bus(unit_of_work.commands)
                policy = self._execution_policy.resolve(
                    unit_of_work.execution_settings,
                    execution_target=scope.execution_target,
                )
                dispatch = (
                    None
                    if definition_changed
                    else bus.submit(
                        CommandRequest(
                            tool_name=definition.name,
                            actor="assistant",
                            scope=scope,
                            payload={
                                "arguments": arguments,
                                "definition_digest": definition.definition_digest,
                            },
                            idempotency_key=(
                                f"assistant:{turn.id}:tool:{invocation.argument_hash}"
                            ),
                        ),
                        profile=policy.profile,
                        capability_overrides=dict(policy.capability_overrides),
                        sandbox_healthy=policy.sandbox_healthy,
                    )
                )
                if dispatch is None:
                    pass
                elif not dispatch.accepted or dispatch.run is None:
                    invocation.reject(
                        error_code=dispatch.error_code or "TOOL_REJECTED",
                    )
                    unit_of_work.assistant.save_tool_invocation(invocation)
                    if dispatch.run is not None:
                        self._tool_trace.start_in_unit(
                            unit_of_work,
                            turn=turn,
                            run=dispatch.run,
                            public_intent=public_intent,
                            tool_summary=tool_summary,
                            failed=True,
                        )
                    unit_of_work.commit()
                    running = None
                elif dispatch.requires_approval:
                    invocation.queue(command_run_id=dispatch.run.id)
                    unit_of_work.assistant.save_tool_invocation(invocation)
                    approval = Approval.create(
                        task_id=turn.task_id,
                        command_run_id=dispatch.run.id,
                        tool_invocation_id=invocation.id,
                        requested_by="assistant",
                        reason=f"Run {definition.description}",
                    )
                    unit_of_work.state.save_approval(approval)
                    if task.status is TaskStatus.EXECUTING:
                        task.transition_to(TaskStatus.AWAITING_APPROVAL)
                        unit_of_work.state.save_task(task)
                    self._tool_trace.start_in_unit(
                        unit_of_work,
                        turn=turn,
                        run=dispatch.run,
                        public_intent=public_intent,
                        tool_summary=tool_summary,
                        approval_required=True,
                    )
                    unit_of_work.commit()
                    return True, None
                else:
                    running = bus.start(
                        dispatch.run.id,
                        lease_until=assistant_command_lease_until(),
                    )
                    invocation.queue(command_run_id=running.id)
                    invocation.start()
                    unit_of_work.assistant.save_tool_invocation(invocation)
                    self._tool_trace.start_in_unit(
                        unit_of_work,
                        turn=turn,
                        run=running,
                        public_intent=public_intent,
                        tool_summary=tool_summary,
                    )
                    unit_of_work.commit()
        if duplicate:
            message = self._append_tool_message(
                turn_id=turn_id,
                tool_name=definition.name,
                tool_call_id=candidate.call_id,
                content="Repeated tool arguments were rejected as a duplicate.",
                rejected=True,
            )
            return False, message
        if running is None:
            message = self._append_tool_message(
                turn_id=turn_id,
                tool_name=definition.name,
                tool_call_id=candidate.call_id,
                content="Tool execution was rejected by policy.",
                rejected=True,
            )
            return False, message

        return False, self._execute_running_tool(
            turn_id=turn_id,
            invocation=invocation,
            running=running,
            definition=definition,
            scope=scope,
            arguments=arguments,
            cancellation=cancellation,
        )

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
    ) -> Message:
        try:
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
            if callable(execute_with_cancellation):
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
        except (ProviderCancelledError, McpCancelledError):
            if cancellation.is_interrupted:
                self._abandon_running_tool(running)
            else:
                self._cancel_running_tool(invocation, running)
            raise
        except Exception as error:
            error_code = str(
                getattr(
                    error,
                    "error_code",
                    getattr(error, "code", "CAPABILITY_NOT_AVAILABLE"),
                )
            )
            with self._unit_of_work_factory() as unit_of_work:
                expected_status = invocation.status
                invocation.fail(error_code=error_code)
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
                    content=f"Tool execution failed ({error_code}).",
                    rejected=True,
                )
                unit_of_work.commit()
            return message

        with self._unit_of_work_factory() as unit_of_work:
            persisted_turn = require_turn(unit_of_work, turn_id)
            if (
                cancellation.is_cancelled
                or persisted_turn.status is not AssistantTurnStatus.WAITING_FOR_TOOL
            ):
                self._cancel_running_tool_in_unit(unit_of_work, invocation, running)
                unit_of_work.commit()
                raise ProviderCancelledError("Assistant Turn is no longer active")
            expected_status = invocation.status
            invocation.complete(
                public_summary=result.public_summary,
                model_content=result.model_content,
                artifact_ids=result.artifact_ids,
            )
            unit_of_work.assistant.update_tool_invocation(
                invocation,
                expected_status=expected_status,
            )
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
                },
                lease_owner=running.lease_owner,
                lease_fence=running.lease_fence,
            )
            message = self._append_tool_message_in_unit(
                unit_of_work,
                turn_id=turn_id,
                tool_name=definition.name,
                tool_call_id=invocation.provider_call_id,
                content=result.model_content,
                rejected=False,
            )
            unit_of_work.commit()
        return message

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

    def _durable_tool_context(self, turn_id: UUID) -> tuple[ModelMessage, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            invocations = unit_of_work.assistant.list_tool_invocations(turn_id)
        return durable_tool_context(invocations)

    def _next_model_round(self, turn_id: UUID) -> int:
        with self._unit_of_work_factory() as unit_of_work:
            invocations = unit_of_work.assistant.list_tool_invocations(turn_id)
        return max((invocation.model_round for invocation in invocations), default=0) + 1

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

    @staticmethod
    def _model_tool_call(candidate: ToolCandidate) -> ModelToolCall:
        try:
            arguments = candidate.arguments()
        except ToolCandidateError:
            arguments = {}
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

    def _resume_after_tools(self, turn_id: UUID) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            expected_status = turn.status
            expected_revision = turn.cancellation_revision
            turn.resume()
            unit_of_work.assistant.update_turn(
                turn,
                expected_status=expected_status,
                expected_cancellation_revision=expected_revision,
            )
            unit_of_work.commit()


__all__ = ["AssistantApplication"]
