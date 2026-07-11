from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from fairy_core.assistant.candidates import ToolCandidate, arguments_for_definition
from fairy_core.assistant.context import AssistantContextBuilder
from fairy_core.assistant.durable_context import durable_tool_context
from fairy_core.assistant.models import (
    AssistantTurn,
    AssistantTurnStatus,
    Message,
    MessageRole,
    MessageVisibility,
    ToolInvocation,
    ToolInvocationStatus,
)
from fairy_core.assistant.tools import (
    DIRECT_ANSWER_TOOL_NAME,
    ToolCandidateError,
    ToolExecutor,
    UnavailableToolExecutor,
    direct_answer,
    tool_message_content,
)
from fairy_core.commanding import CommandRun, CommandStatus, EventVisibility
from fairy_core.commanding.bus import CommandBus, CommandRequest
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.commanding.settings import ExecutionPolicyResolver
from fairy_core.domain.execution import Approval
from fairy_core.domain.models import TaskStatus
from fairy_core.mcp.ports import McpCancelledError
from fairy_core.perception import ImageAttachmentStore
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import (
    CancellationToken,
    ModelDeltaKind,
    ModelMessage,
    ModelRequest,
    ModelRole,
    ModelToolCall,
    ProviderCancelledError,
    ProviderError,
    ProviderRegistry,
    ProviderUnavailableError,
)

_MAX_MODEL_ROUNDS = 3
_MAX_TOOL_INVOCATIONS = 8
_MAX_ASSISTANT_CHARACTERS = 1_000_000


class AssistantApplication:
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
        turn = self._get_turn(turn_id)
        if turn.status in {
            AssistantTurnStatus.COMPLETED,
            AssistantTurnStatus.CANCELLED,
            AssistantTurnStatus.FAILED,
        }:
            self._image_attachments.release(turn_id)
            return turn
        all_text: list[str] = []
        usage: dict[str, int] = {}
        tool_count = self._tool_count(turn_id)
        current_run: CommandRun | None = None
        chunk_index = 0
        model_round_start = 1
        ephemeral_context: list[ModelMessage] = []
        try:
            if turn.status is AssistantTurnStatus.WAITING_FOR_TOOL:
                if self._resume_pending_tool(turn_id, cancellation):
                    return self._get_turn(turn_id)
                self._resume_after_tools(turn_id)
                ephemeral_context.extend(self._durable_tool_context(turn_id))
                model_round_start = self._next_model_round(turn_id)
            for model_round in range(model_round_start, _MAX_MODEL_ROUNDS + 1):
                cancellation.raise_if_cancelled()
                turn, current_run = self._start_model_round(turn_id, model_round)
                profile = self._providers.profile(turn.profile_id)
                context = self._context.build(
                    turn,
                    provider_capabilities=profile.capabilities,
                )
                request = ModelRequest.create(
                    profile_id=turn.profile_id,
                    messages=(*context.messages, *ephemeral_context),
                    tools=context.tools,
                    required_capabilities=context.required_capabilities,
                    max_output_tokens=4_096,
                )
                offered_definitions = {
                    definition.name: definition for definition in context.tool_definitions
                }
                candidates: dict[str, ToolCandidate] = {}
                round_text: list[str] = []
                for delta in self._providers.stream(request, cancellation):
                    cancellation.raise_if_cancelled()
                    self._ensure_turn_active(turn_id)
                    if delta.kind is ModelDeltaKind.TEXT:
                        assert delta.text is not None
                        if sum(map(len, all_text)) + len(delta.text) > _MAX_ASSISTANT_CHARACTERS:
                            raise ValueError("assistant output exceeds the durable message limit")
                        chunk_index += 1
                        self._append_delta(
                            turn_id=turn_id,
                            run=current_run,
                            model_round=model_round,
                            chunk_index=chunk_index,
                            text=delta.text,
                        )
                        round_text.append(delta.text)
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
                    if tool_count + len(external_candidates) > _MAX_TOOL_INVOCATIONS:
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
                            return self._get_turn(turn_id)
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
            return self._cancel_turn(turn_id, current_run)
        except ProviderUnavailableError:
            return self._fail_turn(
                turn_id,
                current_run,
                error_code="CAPABILITY_NOT_AVAILABLE",
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
            self._release_terminal_images(turn_id)

    def _release_terminal_images(self, turn_id: UUID) -> None:
        try:
            turn = self._get_turn(turn_id)
        except KeyError:
            return
        if turn.status in {
            AssistantTurnStatus.COMPLETED,
            AssistantTurnStatus.CANCELLED,
            AssistantTurnStatus.FAILED,
        }:
            self._image_attachments.release(turn_id)

    def _start_model_round(
        self,
        turn_id: UUID,
        model_round: int,
    ) -> tuple[AssistantTurn, CommandRun]:
        with self._unit_of_work_factory() as unit_of_work:
            turn = _require_turn(unit_of_work, turn_id)
            task = _require_task(unit_of_work, turn.task_id)
            scope = self._scope_resolver(unit_of_work.state, task)
            started = turn.status is AssistantTurnStatus.CREATED
            if started:
                expected_status = turn.status
                expected_revision = turn.cancellation_revision
                turn.start()
                unit_of_work.assistant.update_turn(
                    turn,
                    expected_status=expected_status,
                    expected_cancellation_revision=expected_revision,
                )
                if task.status is TaskStatus.PLANNING:
                    task.transition_to(TaskStatus.EXECUTING)
                    unit_of_work.state.save_task(task)
                elif task.status is TaskStatus.FAILED:
                    task.transition_to(TaskStatus.REPAIRING)
                    task.transition_to(TaskStatus.EXECUTING)
                    unit_of_work.state.save_task(task)
            elif turn.status is not AssistantTurnStatus.RUNNING:
                raise ValueError(f"Assistant Turn cannot run from {turn.status.value}")
            bus = self._command_bus(unit_of_work.commands)
            policy = self._execution_policy.resolve(
                unit_of_work.execution_settings,
                execution_target=scope.execution_target,
            )
            dispatch = bus.submit(
                CommandRequest(
                    tool_name="model.generate",
                    actor="assistant",
                    scope=scope,
                    payload={
                        "turn_id": str(turn.id),
                        "profile_id": turn.profile_id,
                        "model_round": model_round,
                    },
                    idempotency_key=f"assistant:{turn.id}:model:{model_round}",
                ),
                profile=policy.profile,
                capability_overrides=dict(policy.capability_overrides),
                sandbox_healthy=policy.sandbox_healthy,
            )
            if not dispatch.accepted or dispatch.run is None:
                raise ProviderUnavailableError(
                    dispatch.error_code or "model provider command was rejected"
                )
            if dispatch.requires_approval:
                raise RuntimeError("model generation cannot require approval")
            if dispatch.run.status is not CommandStatus.QUEUED:
                raise RuntimeError("model generation command is not queued")
            running = bus.start(dispatch.run.id)
            if started:
                unit_of_work.commands.append_event(
                    run_id=running.id,
                    event_type="assistant.turn.started",
                    visibility=EventVisibility.USER,
                    message="Assistant turn started",
                    payload={"turn_id": str(turn.id), "profile_id": turn.profile_id},
                    lease_owner=running.lease_owner,
                    lease_fence=running.lease_fence,
                )
            unit_of_work.commit()
        return turn, running

    def _append_delta(
        self,
        *,
        turn_id: UUID,
        run: CommandRun,
        model_round: int,
        chunk_index: int,
        text: str,
    ) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            turn = _require_turn(unit_of_work, turn_id)
            if turn.status is not AssistantTurnStatus.RUNNING:
                raise ProviderCancelledError("Assistant Turn is no longer running")
            unit_of_work.commands.append_event(
                run_id=run.id,
                event_type="assistant.message.delta",
                visibility=EventVisibility.USER,
                message="Assistant response updated",
                payload={
                    "turn_id": str(turn_id),
                    "model_round": model_round,
                    "chunk_index": chunk_index,
                    "text": text,
                },
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
            )
            unit_of_work.commit()

    def _wait_for_tools(
        self,
        *,
        turn_id: UUID,
        run: CommandRun,
        candidate_count: int,
    ) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            turn = _require_turn(unit_of_work, turn_id)
            expected_status = turn.status
            expected_revision = turn.cancellation_revision
            turn.wait_for_tool()
            unit_of_work.assistant.update_turn(
                turn,
                expected_status=expected_status,
                expected_cancellation_revision=expected_revision,
            )
            self._command_bus(unit_of_work.commands).complete(
                run.id,
                output={"tool_candidates": candidate_count},
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
            )
            unit_of_work.commit()

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
        self._ensure_turn_waiting_for_tool(turn_id)
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
            turn = _require_turn(unit_of_work, turn_id)
            task = _require_task(unit_of_work, turn.task_id)
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
                    unit_of_work.commit()
                    return True, None
                else:
                    running = bus.start(dispatch.run.id)
                    invocation.queue(command_run_id=running.id)
                    invocation.start()
                    unit_of_work.assistant.save_tool_invocation(invocation)
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
            self._ensure_turn_waiting_for_tool(turn_id)
            current_definition = self._registry.get(definition.name)
            if (
                current_definition is None
                or current_definition.definition_digest != definition.definition_digest
            ):
                error = RuntimeError("tool definition changed before execution")
                error.error_code = "MCP_SCHEMA_CHANGED"  # type: ignore[attr-defined]
                raise error
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
            self._ensure_turn_waiting_for_tool(turn_id)
        except (ProviderCancelledError, McpCancelledError):
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
            persisted_turn = _require_turn(unit_of_work, turn_id)
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
            turn = _require_turn(unit_of_work, turn_id)
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
                unit_of_work.commit()
                return False
            task = _require_task(unit_of_work, turn.task_id)
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
                unit_of_work.commit()
                return False
            bus = self._command_bus(unit_of_work.commands)
            if command.status is CommandStatus.QUEUED:
                running = bus.start(command.id)
            elif command.status is CommandStatus.RUNNING:
                if command.lease_until is None or command.lease_until > datetime.now(UTC):
                    return True
                running = bus.start(command.id)
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
                unit_of_work.commit()
                return False

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
        turn = _require_turn(unit_of_work, turn_id)
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
            turn = _require_turn(unit_of_work, turn_id)
            expected_status = turn.status
            expected_revision = turn.cancellation_revision
            turn.resume()
            unit_of_work.assistant.update_turn(
                turn,
                expected_status=expected_status,
                expected_cancellation_revision=expected_revision,
            )
            unit_of_work.commit()

    def _complete_turn(
        self,
        *,
        turn_id: UUID,
        run: CommandRun,
        content: str,
        usage: dict[str, int],
    ) -> AssistantTurn:
        with self._unit_of_work_factory() as unit_of_work:
            turn = _require_turn(unit_of_work, turn_id)
            task = _require_task(unit_of_work, turn.task_id)
            message = Message.create(
                conversation_id=turn.conversation_id,
                task_id=turn.task_id,
                turn_id=turn.id,
                sequence=unit_of_work.assistant.next_message_sequence(turn.conversation_id),
                role=MessageRole.ASSISTANT,
                visibility=MessageVisibility.USER,
                content=content,
            )
            expected_status = turn.status
            expected_revision = turn.cancellation_revision
            turn.complete(usage=usage)
            unit_of_work.assistant.append_message(message)
            unit_of_work.assistant.update_turn(
                turn,
                expected_status=expected_status,
                expected_cancellation_revision=expected_revision,
            )
            if task.project_id is None and task.status is TaskStatus.EXECUTING:
                task.transition_to(TaskStatus.REVIEWING)
                task.transition_to(TaskStatus.READY)
                unit_of_work.state.save_task(task)
            unit_of_work.commands.append_event(
                run_id=run.id,
                event_type="assistant.turn.completed",
                visibility=EventVisibility.USER,
                message="Assistant turn completed",
                payload={
                    "turn_id": str(turn.id),
                    "message_id": str(message.id),
                    "usage": usage,
                },
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
            )
            self._command_bus(unit_of_work.commands).complete(
                run.id,
                output={"turn_id": str(turn.id), "message_id": str(message.id)},
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
            )
            unit_of_work.commit()
        return turn

    def _cancel_turn(
        self,
        turn_id: UUID,
        run: CommandRun | None,
    ) -> AssistantTurn:
        with self._unit_of_work_factory() as unit_of_work:
            turn = _require_turn(unit_of_work, turn_id)
            if turn.status is not AssistantTurnStatus.CANCELLED:
                expected_status = turn.status
                expected_revision = turn.cancellation_revision
                turn.cancel()
                unit_of_work.assistant.update_turn(
                    turn,
                    expected_status=expected_status,
                    expected_cancellation_revision=expected_revision,
                )
            if run is not None:
                persisted_run = unit_of_work.commands.get_run(run.id)
                if persisted_run is not None and persisted_run.status in {
                    CommandStatus.QUEUED,
                    CommandStatus.WAITING_APPROVAL,
                    CommandStatus.RUNNING,
                }:
                    if persisted_run.status is CommandStatus.RUNNING:
                        unit_of_work.commands.append_event(
                            run_id=run.id,
                            event_type="assistant.turn.cancelled",
                            visibility=EventVisibility.USER,
                            message="Assistant turn cancelled",
                            payload={"turn_id": str(turn.id)},
                            lease_owner=run.lease_owner,
                            lease_fence=run.lease_fence,
                        )
                    self._command_bus(unit_of_work.commands).cancel(
                        run.id,
                        lease_owner=(
                            run.lease_owner
                            if persisted_run.status is CommandStatus.RUNNING
                            else None
                        ),
                        lease_fence=(
                            run.lease_fence
                            if persisted_run.status is CommandStatus.RUNNING
                            else None
                        ),
                    )
            task = _require_task(unit_of_work, turn.task_id)
            if task.status in {
                TaskStatus.PLANNING,
                TaskStatus.AWAITING_APPROVAL,
                TaskStatus.EXECUTING,
                TaskStatus.INSTALLING,
                TaskStatus.PREVIEWING,
                TaskStatus.REVIEWING,
                TaskStatus.REPAIRING,
            }:
                task.transition_to(TaskStatus.FAILED)
                unit_of_work.state.save_task(task)
            unit_of_work.commit()
        return turn

    def _fail_turn(
        self,
        turn_id: UUID,
        run: CommandRun | None,
        *,
        error_code: str,
    ) -> AssistantTurn:
        with self._unit_of_work_factory() as unit_of_work:
            turn = _require_turn(unit_of_work, turn_id)
            if turn.status not in {
                AssistantTurnStatus.COMPLETED,
                AssistantTurnStatus.CANCELLED,
                AssistantTurnStatus.FAILED,
            }:
                expected_status = turn.status
                expected_revision = turn.cancellation_revision
                turn.fail(error_code=error_code)
                unit_of_work.assistant.update_turn(
                    turn,
                    expected_status=expected_status,
                    expected_cancellation_revision=expected_revision,
                )
            if run is not None:
                persisted_run = unit_of_work.commands.get_run(run.id)
                if persisted_run is not None and persisted_run.status is CommandStatus.RUNNING:
                    unit_of_work.commands.append_event(
                        run_id=run.id,
                        event_type="assistant.turn.failed",
                        visibility=EventVisibility.USER,
                        message="Assistant turn failed",
                        payload={
                            "turn_id": str(turn.id),
                            "error_code": error_code,
                        },
                        lease_owner=run.lease_owner,
                        lease_fence=run.lease_fence,
                    )
                    self._command_bus(unit_of_work.commands).fail(
                        run.id,
                        error_code=error_code,
                        lease_owner=run.lease_owner,
                        lease_fence=run.lease_fence,
                    )
            task = _require_task(unit_of_work, turn.task_id)
            if task.status in {
                TaskStatus.PLANNING,
                TaskStatus.AWAITING_APPROVAL,
                TaskStatus.EXECUTING,
                TaskStatus.INSTALLING,
                TaskStatus.PREVIEWING,
                TaskStatus.REVIEWING,
                TaskStatus.REPAIRING,
            }:
                task.transition_to(TaskStatus.FAILED)
                unit_of_work.state.save_task(task)
            unit_of_work.commit()
        return turn

    def _ensure_turn_active(self, turn_id: UUID) -> None:
        turn = self._get_turn(turn_id)
        if turn.status is not AssistantTurnStatus.RUNNING:
            raise ProviderCancelledError("Assistant Turn is no longer running")

    def _ensure_turn_waiting_for_tool(self, turn_id: UUID) -> None:
        turn = self._get_turn(turn_id)
        if turn.status is not AssistantTurnStatus.WAITING_FOR_TOOL:
            raise ProviderCancelledError("Assistant Turn is no longer waiting for a tool")

    def _tool_count(self, turn_id: UUID) -> int:
        with self._unit_of_work_factory() as unit_of_work:
            return len(unit_of_work.assistant.list_tool_invocations(turn_id))

    def _get_turn(self, turn_id: UUID) -> AssistantTurn:
        with self._unit_of_work_factory() as unit_of_work:
            return _require_turn(unit_of_work, turn_id)

    def _command_bus(self, ledger) -> CommandBus:
        return CommandBus(
            registry=self._registry,
            policy=self._policy,
            ledger=ledger,
        )


def _require_turn(unit_of_work, turn_id: UUID) -> AssistantTurn:
    turn = unit_of_work.assistant.get_turn(turn_id)
    if turn is None:
        raise KeyError(f"Assistant Turn not found: {turn_id}")
    return turn


def _require_task(unit_of_work, task_id: UUID):
    task = unit_of_work.state.get_task(task_id)
    if task is None:
        raise KeyError(f"task not found: {task_id}")
    return task


__all__ = ["AssistantApplication"]
