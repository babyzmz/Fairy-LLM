from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fairy_core.assistant import limits
from fairy_core.assistant.events import append_message_created
from fairy_core.assistant.models import AssistantTurn, AssistantTurnStatus, MessageRole
from fairy_core.assistant.plan_budget import consume_model_budget
from fairy_core.assistant.routing import (
    DEEPSEEK_MODEL_ID,
    GLM_MODEL_ID,
    KIMI_MODEL_ID,
    NEMOTRON_FREE_MODEL_ID,
    QWEN_FREE_MODEL_ID,
    RoutingDecision,
    RoutingTaskKind,
    auto_routing_decision,
    build_router_request,
    manual_routing_decision,
    parse_router_output,
)
from fairy_core.assistant.turn_reader import require_task, require_turn
from fairy_core.commanding import CommandRun, CommandStatus, EventVisibility
from fairy_core.commanding.bus import CommandRequest
from fairy_core.domain.execution import Approval
from fairy_core.domain.models import TaskStatus
from fairy_core.mcp.ports import McpCancelledError
from fairy_core.model_catalog.models import (
    MODEL_ALLOWLIST_BY_ID,
    ModelAvailability,
    ModelCatalogSnapshot,
    ModelSelectionMode,
    ProviderCredentialStatus,
    baseline_catalog,
)
from fairy_core.providers import (
    CancellationToken,
    ModelDeltaKind,
    ModelExecutionRole,
    ModelMessage,
    ModelRequest,
    ModelRole,
    ProviderAuthenticationError,
    ProviderCancelledError,
    ProviderCapability,
    ProviderContentRejectedError,
    ProviderContextLengthError,
    ProviderError,
    ProviderNetworkError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class AssistantRoutingMixin:
    def _ensure_routing(
        self,
        turn: AssistantTurn,
        cancellation: CancellationToken,
    ) -> RoutingDecision | None:
        if turn.model_selection is None:
            return turn.routing_decision
        if turn.routing_decision is not None:
            return turn.routing_decision
        user_request, catalog = self._routing_inputs(turn.id)
        if turn.model_selection.mode is ModelSelectionMode.MANUAL:
            decision = manual_routing_decision(
                selection=turn.model_selection,
                catalog=catalog,
                user_request=user_request,
            )
            self._require_available_route(decision, catalog)
            self._bind_routing(turn.id, decision)
            return decision
        return self._run_auto_router(
            turn=turn,
            user_request=user_request,
            catalog=catalog,
            cancellation=cancellation,
        )

    def _routing_inputs(self, turn_id: UUID) -> tuple[str, ModelCatalogSnapshot]:
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            user_message = unit_of_work.assistant.message_for_turn(
                turn.id,
                MessageRole.USER,
            )
            if user_message is None:
                raise RuntimeError("Assistant Turn has no user message")
            catalog = unit_of_work.model_catalog.get_catalog()
        if catalog is None:
            catalog = baseline_catalog(
                now=datetime.now(UTC),
                credential_status=ProviderCredentialStatus.CONFIGURED,
            )
        return user_message.content, catalog

    def _run_auto_router(
        self,
        *,
        turn: AssistantTurn,
        user_request: str,
        catalog: ModelCatalogSnapshot,
        cancellation: CancellationToken,
    ) -> RoutingDecision:
        selection = turn.model_selection
        if selection is None or selection.mode is not ModelSelectionMode.AUTO:
            raise ValueError("Auto router requires an Auto model selection snapshot")
        primary = self._providers.profile_for_model(DEEPSEEK_MODEL_ID)
        fallback_ids = self._profile_ids_for_models(
            (GLM_MODEL_ID,),
            required_capabilities=frozenset(
                {ProviderCapability.TEXT, ProviderCapability.STRUCTURED_OUTPUT}
            ),
            exclude_profile_id=primary.id,
        )
        _started_turn, run = self._start_model_round(
            turn.id,
            1,
            profile_id=primary.id,
            model_role=ModelExecutionRole.COORDINATOR,
        )
        try:
            request = build_router_request(
                profile_id=primary.id,
                user_request=user_request,
                attachment_count=len(self._image_attachments.for_turn(turn.id)),
                selection=selection,
                fallback_profile_ids=fallback_ids,
            )
            chunks: list[str] = []
            for delta in self._providers.stream(
                request,
                cancellation,
                on_attempt=self._provider_attempts.observer(turn.id, 1),
            ):
                cancellation.raise_if_cancelled()
                self._turns.require_active(turn.id)
                if delta.kind is ModelDeltaKind.TEXT:
                    assert delta.text is not None
                    chunks.append(delta.text)
                    if sum(map(len, chunks)) > 16_384:
                        raise ProviderProtocolError("router response is too large")
                elif delta.kind is ModelDeltaKind.TOOL_CALL:
                    raise ProviderProtocolError("router cannot call tools")
            routed = parse_router_output("".join(chunks))
            decision = auto_routing_decision(
                routed=routed,
                catalog=catalog,
                user_request=user_request,
                attachment_count=len(self._image_attachments.for_turn(turn.id)),
                allow_free_fallback=selection.allow_free_fallback,
            )
            self._require_available_route(decision, catalog)
            self._bind_routing(turn.id, decision, run=run)
            return decision
        except (ProviderCancelledError, McpCancelledError):
            self._cancel_model_run(run)
            raise
        except Exception:
            self._fail_model_run(run, error_code="PROVIDER_ROUTING_FAILED")
            raise

    def _bind_routing(
        self,
        turn_id: UUID,
        decision: RoutingDecision,
        *,
        run: CommandRun | None = None,
    ) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            expected_status = turn.status
            expected_revision = turn.cancellation_revision
            turn.bind_routing(decision)
            unit_of_work.assistant.update_turn(
                turn,
                expected_status=expected_status,
                expected_cancellation_revision=expected_revision,
            )
            if run is not None:
                self._append_route_selected_event(
                    unit_of_work,
                    run=run,
                    decision=decision,
                )
                self._command_bus(unit_of_work.commands).complete(
                    run.id,
                    output={
                        "turn_id": str(turn.id),
                        "task_kind": decision.task_kind.value,
                        "complexity": decision.complexity.value,
                    },
                    lease_owner=run.lease_owner,
                    lease_fence=run.lease_fence,
                )
            unit_of_work.commit()

    def _request_budget_approval(
        self,
        turn_id: UUID,
        decision: RoutingDecision,
    ) -> AssistantTurn:
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            if turn.budget_approval_run_id is not None:
                return turn
            task = require_task(unit_of_work, turn.task_id)
            scope = self._scope_resolver(unit_of_work.state, task)
            policy = self._execution_policy.resolve(
                unit_of_work.execution_settings,
                execution_target=scope.execution_target,
            )
            bus = self._command_bus(unit_of_work.commands)
            dispatch = bus.submit(
                CommandRequest(
                    tool_name="model.generate.expensive",
                    actor="assistant",
                    scope=scope,
                    payload={
                        "turn_id": str(turn.id),
                        "estimated_cost_usd": decision.estimated_cost_usd,
                        "execution_model_count": len(set(decision.execution_model_ids)),
                    },
                    idempotency_key=f"assistant:{turn.id}:model-budget",
                ),
                profile=policy.profile,
                capability_overrides=dict(policy.capability_overrides),
                sandbox_healthy=policy.sandbox_healthy,
            )
            if not dispatch.accepted or not dispatch.requires_approval or dispatch.run is None:
                raise ProviderUnavailableError(
                    dispatch.error_code or "model budget approval was rejected"
                )
            run = dispatch.run
            approval = unit_of_work.state.find_approval_by_command_run_id(run.id)
            if approval is None:
                approval = Approval.create(
                    task_id=turn.task_id,
                    command_run_id=run.id,
                    requested_by="assistant",
                    reason=self._budget_approval_reason(decision),
                )
                unit_of_work.state.save_approval(approval)

            expected_status = turn.status
            expected_revision = turn.cancellation_revision
            started = turn.status is AssistantTurnStatus.CREATED
            if started:
                turn.start()
                if task.status is TaskStatus.PLANNING:
                    task.transition_to(TaskStatus.EXECUTING)
            if turn.status is not AssistantTurnStatus.RUNNING:
                raise ValueError("Assistant Turn cannot request model budget approval")
            turn.wait_for_budget_approval(command_run_id=run.id)
            if task.status is TaskStatus.EXECUTING:
                task.transition_to(TaskStatus.AWAITING_APPROVAL)
            unit_of_work.assistant.update_turn(
                turn,
                expected_status=expected_status,
                expected_cancellation_revision=expected_revision,
            )
            unit_of_work.state.save_task(task)
            if started:
                user_message = unit_of_work.assistant.message_for_turn(
                    turn.id,
                    MessageRole.USER,
                )
                if user_message is not None:
                    append_message_created(
                        unit_of_work.commands,
                        run=run,
                        message=user_message,
                    )
                unit_of_work.commands.append_event(
                    run_id=run.id,
                    event_type="assistant.turn.started",
                    visibility=EventVisibility.USER,
                    message="Assistant turn started",
                    payload={
                        "turn_id": str(turn.id),
                        "profile_id": turn.profile_id,
                        "selection_mode": turn.model_selection.mode.value,
                    },
                )
                self._append_route_selected_event(
                    unit_of_work,
                    run=run,
                    decision=decision,
                )
            unit_of_work.commands.append_event(
                run_id=run.id,
                event_type="assistant.budget.approval_requested",
                visibility=EventVisibility.USER,
                message="Model budget approval requested",
                payload={
                    "turn_id": str(turn.id),
                    "approval_id": str(approval.id),
                    "estimated_cost_usd": decision.estimated_cost_usd,
                },
            )
            unit_of_work.commit()
        return turn

    def _resume_budget_approval(
        self,
        turn_id: UUID,
        cancellation: CancellationToken,
    ) -> str:
        cancellation.raise_if_cancelled()
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            if turn.budget_approval_run_id is None:
                raise ValueError("Assistant Turn has no model budget approval")
            command = unit_of_work.commands.get_run(turn.budget_approval_run_id)
            if command is None:
                raise RuntimeError("model budget approval CommandRun is missing")
            if command.status is CommandStatus.WAITING_APPROVAL:
                return "waiting"
            if command.status in {CommandStatus.REJECTED, CommandStatus.CANCELLED}:
                return "rejected"
            bus = self._command_bus(unit_of_work.commands)
            if command.status is CommandStatus.QUEUED:
                running = bus.start(command.id)
            elif command.status is CommandStatus.RUNNING:
                if command.lease_until is None or command.lease_until > datetime.now(UTC):
                    return "waiting"
                running = bus.start(command.id)
            elif command.status is CommandStatus.SUCCEEDED:
                running = None
            else:
                raise ProviderUnavailableError(
                    f"model budget approval ended as {command.status.value}"
                )

            if running is not None:
                unit_of_work.commands.append_event(
                    run_id=running.id,
                    event_type="assistant.budget.approved",
                    visibility=EventVisibility.USER,
                    message="Model budget approved",
                    payload={"turn_id": str(turn.id)},
                    lease_owner=running.lease_owner,
                    lease_fence=running.lease_fence,
                )
                bus.complete(
                    running.id,
                    output={"authorized": True, "turn_id": str(turn.id)},
                    lease_owner=running.lease_owner,
                    lease_fence=running.lease_fence,
                )
            expected_status = turn.status
            expected_revision = turn.cancellation_revision
            turn.resume_budget_approval()
            unit_of_work.assistant.update_turn(
                turn,
                expected_status=expected_status,
                expected_cancellation_revision=expected_revision,
            )
            task = require_task(unit_of_work, turn.task_id)
            if task.status is TaskStatus.AWAITING_APPROVAL:
                task.transition_to(TaskStatus.EXECUTING)
                unit_of_work.state.save_task(task)
            unit_of_work.commit()
        return "approved"

    def _execution_profile(
        self,
        turn: AssistantTurn,
        decision: RoutingDecision | None,
    ):
        if decision is None:
            return self._providers.profile(turn.profile_id)
        return self._providers.profile_for_model(decision.primary_model_id)

    @staticmethod
    def _require_available_route(
        decision: RoutingDecision,
        catalog: ModelCatalogSnapshot,
    ) -> None:
        entries = {entry.model_id: entry for entry in catalog.entries}
        for model_id in decision.execution_model_ids:
            entry = entries.get(model_id)
            if entry is None or entry.availability is ModelAvailability.UNAVAILABLE:
                raise ProviderUnavailableError(f"routed model {model_id!r} is unavailable")

    def _complete_model_round(
        self,
        run: CommandRun,
        *,
        output: dict[str, object],
    ) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            persisted = unit_of_work.commands.get_run(run.id)
            if persisted is None:
                raise RuntimeError("model CommandRun is missing")
            if persisted.status is CommandStatus.SUCCEEDED:
                return
            if persisted.status is not CommandStatus.RUNNING:
                raise RuntimeError("model CommandRun is not running")
            self._command_bus(unit_of_work.commands).complete(
                run.id,
                output=output,
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
            )
            unit_of_work.commit()

    def _review_and_complete(
        self,
        *,
        turn_id: UUID,
        decision: RoutingDecision,
        source_messages: tuple[ModelMessage, ...],
        draft: str,
        model_round: int,
        chunk_index: int,
        usage: dict[str, int],
        cancellation: CancellationToken,
    ) -> AssistantTurn:
        if decision.reviewer_model_id is None:
            raise ValueError("reviewer model is not configured")
        turn = self._turns.get(turn_id)
        profile = self._providers.profile_for_model(decision.reviewer_model_id)
        _turn, run = self._start_model_round(
            turn_id,
            model_round,
            profile_id=profile.id,
            model_role=ModelExecutionRole.REVIEWER,
        )
        request = ModelRequest.create(
            profile_id=profile.id,
            messages=(
                ModelMessage.create(
                    role=ModelRole.SYSTEM,
                    content=(
                        "Review the proposed response for correctness, completeness, and safety. "
                        "Return only the polished final answer. Do not describe the review, expose "
                        "hidden reasoning, or call tools."
                    ),
                ),
                *self._review_source_messages(source_messages),
                ModelMessage.create(role=ModelRole.ASSISTANT, content=draft),
                ModelMessage.create(
                    role=ModelRole.USER,
                    content="Return the single final answer for the original request.",
                ),
            ),
            tools=(),
            required_capabilities=frozenset({ProviderCapability.TEXT}),
            max_output_tokens=decision.estimated_output_tokens,
            model_role=ModelExecutionRole.REVIEWER,
            fallback_profile_ids=self._fallback_profile_ids_for_model(
                turn=turn,
                decision=decision,
                model_id=decision.reviewer_model_id,
                required_capabilities=frozenset({ProviderCapability.TEXT}),
            ),
            allow_profile_fallback=False,
            require_parameters=True,
            deny_data_collection=True,
            zero_data_retention=(
                turn.model_selection.zero_data_retention
                if turn.model_selection is not None
                else False
            ),
        )
        reviewed: list[str] = []
        try:
            for delta in self._providers.stream(
                request,
                cancellation,
                on_attempt=self._provider_attempts.observer(turn_id, model_round),
            ):
                cancellation.raise_if_cancelled()
                self._turns.require_active(turn_id)
                if delta.kind is ModelDeltaKind.TEXT:
                    assert delta.text is not None
                    if sum(map(len, reviewed)) + len(delta.text) > (
                        limits.MAX_ASSISTANT_CHARACTERS
                    ):
                        raise ProviderProtocolError("reviewer response is too large")
                    chunk_index += 1
                    self._append_delta(
                        turn_id=turn_id,
                        run=run,
                        model_round=model_round,
                        chunk_index=chunk_index,
                        text=delta.text,
                    )
                    reviewed.append(delta.text)
                elif delta.kind is ModelDeltaKind.TOOL_CALL:
                    raise ProviderProtocolError("reviewer cannot call tools")
                elif delta.kind is ModelDeltaKind.USAGE:
                    for name, value in delta.usage.items():
                        usage[name] = usage.get(name, 0) + value
            if not reviewed:
                return self._fail_turn(
                    turn_id,
                    run,
                    error_code="PROVIDER_PROTOCOL_ERROR",
                )
            return self._complete_turn(
                turn_id=turn_id,
                run=run,
                content="".join(reviewed),
                usage=usage,
            )
        except ProviderCancelledError:
            return self._cancel_turn(turn_id, run)
        except ProviderError as error:
            return self._fail_turn(
                turn_id,
                run,
                error_code=self._provider_error_code(error),
            )
        except ValueError:
            return self._fail_turn(
                turn_id,
                run,
                error_code="PROVIDER_PROTOCOL_ERROR",
            )

    def _fallback_profile_ids(
        self,
        *,
        turn: AssistantTurn,
        decision: RoutingDecision | None,
        required_capabilities: frozenset[ProviderCapability],
    ) -> tuple[str, ...]:
        if decision is None:
            return ()
        return self._fallback_profile_ids_for_model(
            turn=turn,
            decision=decision,
            model_id=decision.primary_model_id,
            required_capabilities=required_capabilities,
        )

    def _fallback_profile_ids_for_model(
        self,
        *,
        turn: AssistantTurn,
        decision: RoutingDecision,
        model_id: str,
        required_capabilities: frozenset[ProviderCapability],
    ) -> tuple[str, ...]:
        selection = turn.model_selection
        if selection is None or selection.mode is ModelSelectionMode.MANUAL:
            return ()
        candidates: list[str] = []
        if model_id in {GLM_MODEL_ID, KIMI_MODEL_ID}:
            candidates.append(DEEPSEEK_MODEL_ID)
        if selection.allow_free_fallback:
            candidates.append(
                QWEN_FREE_MODEL_ID
                if decision.task_kind is RoutingTaskKind.CODE
                else NEMOTRON_FREE_MODEL_ID
            )
        primary = self._providers.profile_for_model(model_id)
        return self._profile_ids_for_models(
            tuple(candidates),
            required_capabilities=required_capabilities,
            exclude_profile_id=primary.id,
        )

    def _profile_ids_for_models(
        self,
        model_ids: tuple[str, ...],
        *,
        required_capabilities: frozenset[ProviderCapability],
        exclude_profile_id: str,
    ) -> tuple[str, ...]:
        result: list[str] = []
        for model_id in model_ids:
            try:
                profile = self._providers.profile_for_model(model_id)
            except ProviderUnavailableError:
                continue
            if (
                profile.id != exclude_profile_id
                and required_capabilities.issubset(profile.capabilities)
                and profile.id not in result
            ):
                result.append(profile.id)
        return tuple(result)

    @staticmethod
    def _review_source_messages(
        messages: tuple[ModelMessage, ...],
    ) -> tuple[ModelMessage, ...]:
        sanitized: list[ModelMessage] = []
        for message in messages:
            if not message.images:
                sanitized.append(message)
                continue
            content = message.content or "The original request included image attachments."
            sanitized.append(
                ModelMessage.create(
                    role=message.role,
                    content=content,
                    name=message.name,
                    tool_call_id=message.tool_call_id,
                    tool_calls=message.tool_calls,
                )
            )
        return tuple(sanitized)

    def _reset_message_projection(
        self,
        *,
        turn_id: UUID,
        run: CommandRun,
        through_chunk_index: int,
    ) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.commands.append_event(
                run_id=run.id,
                event_type="assistant.message.projection_reset",
                visibility=EventVisibility.USER,
                message="Temporary assistant response cleared",
                payload={
                    "turn_id": str(turn_id),
                    "through_chunk_index": through_chunk_index,
                    "reason": "provider_protocol_error",
                },
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
            )
            unit_of_work.commit()

    @staticmethod
    def _provider_error_code(error: ProviderError) -> str:
        if isinstance(error, ProviderAuthenticationError):
            return "PROVIDER_AUTHENTICATION_FAILED"
        if isinstance(error, ProviderRateLimitError):
            return "PROVIDER_RATE_LIMITED"
        if isinstance(error, ProviderTimeoutError):
            return "PROVIDER_TIMEOUT"
        if isinstance(error, ProviderProtocolError):
            return "PROVIDER_PROTOCOL_ERROR"
        if isinstance(error, ProviderContextLengthError):
            return "PROVIDER_CONTEXT_LENGTH_EXCEEDED"
        if isinstance(error, ProviderContentRejectedError):
            return "PROVIDER_CONTENT_REJECTED"
        if isinstance(error, ProviderNetworkError):
            return "PROVIDER_NETWORK_ERROR"
        if isinstance(error, ProviderUnavailableError):
            return "PROVIDER_UNAVAILABLE"
        return "PROVIDER_ERROR"

    @staticmethod
    def _budget_approval_reason(decision: RoutingDecision) -> str:
        if decision.estimated_cost_usd is None:
            estimate = "an unavailable cost estimate"
        else:
            estimate = f"an estimated US${decision.estimated_cost_usd}"
        return (
            f"Authorize {estimate} model route using "
            f"{len(set(decision.execution_model_ids))} execution model(s)"
        )

    @staticmethod
    def _append_route_selected_event(
        unit_of_work,
        *,
        run: CommandRun,
        decision: RoutingDecision,
    ) -> None:
        primary = MODEL_ALLOWLIST_BY_ID[decision.primary_model_id].display_name
        reviewer = (
            MODEL_ALLOWLIST_BY_ID[decision.reviewer_model_id].display_name
            if decision.reviewer_model_id is not None
            else None
        )
        unit_of_work.commands.append_event(
            run_id=run.id,
            event_type="assistant.route.selected",
            visibility=EventVisibility.USER,
            message="Model route selected",
            payload={
                "turn_id": str(run.input_payload["turn_id"]),
                "task_kind": decision.task_kind.value,
                "complexity": decision.complexity.value,
                "primary_model": primary,
                "reviewer_model": reviewer,
                "public_summary": decision.public_summary,
            },
            lease_owner=(run.lease_owner if run.status is CommandStatus.RUNNING else None),
            lease_fence=(run.lease_fence if run.status is CommandStatus.RUNNING else None),
        )

    def _fail_model_run(self, run: CommandRun, *, error_code: str) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            persisted = unit_of_work.commands.get_run(run.id)
            if persisted is not None and persisted.status is CommandStatus.RUNNING:
                self._command_bus(unit_of_work.commands).fail(
                    run.id,
                    error_code=error_code,
                    lease_owner=run.lease_owner,
                    lease_fence=run.lease_fence,
                )
                unit_of_work.commit()

    def _cancel_model_run(self, run: CommandRun) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            persisted = unit_of_work.commands.get_run(run.id)
            if persisted is not None and persisted.status is CommandStatus.RUNNING:
                self._command_bus(unit_of_work.commands).cancel(
                    run.id,
                    lease_owner=run.lease_owner,
                    lease_fence=run.lease_fence,
                )
                unit_of_work.commit()

    def _start_model_round(
        self,
        turn_id: UUID,
        model_round: int,
        *,
        profile_id: str | None = None,
        model_role: ModelExecutionRole = ModelExecutionRole.PRIMARY,
    ) -> tuple[AssistantTurn, CommandRun]:
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            task = require_task(unit_of_work, turn.task_id)
            scope = self._scope_resolver(unit_of_work.state, task)
            selected_profile_id = profile_id or turn.profile_id
            consume_model_budget(unit_of_work, task.id)
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
                        "profile_id": selected_profile_id,
                        "model_round": model_round,
                        "model_role": model_role.value,
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
                user_message = unit_of_work.assistant.message_for_turn(
                    turn.id,
                    MessageRole.USER,
                )
                if user_message is not None:
                    append_message_created(
                        unit_of_work.commands,
                        run=running,
                        message=user_message,
                    )
                unit_of_work.commands.append_event(
                    run_id=running.id,
                    event_type="assistant.turn.started",
                    visibility=EventVisibility.USER,
                    message="Assistant turn started",
                    payload={
                        "turn_id": str(turn.id),
                        "profile_id": selected_profile_id,
                        "selection_mode": (
                            turn.model_selection.mode.value
                            if turn.model_selection is not None
                            else "legacy"
                        ),
                    },
                    lease_owner=running.lease_owner,
                    lease_fence=running.lease_fence,
                )
                if turn.routing_decision is not None and model_role is ModelExecutionRole.PRIMARY:
                    self._append_route_selected_event(
                        unit_of_work,
                        run=running,
                        decision=turn.routing_decision,
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
            turn = require_turn(unit_of_work, turn_id)
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
            turn = require_turn(unit_of_work, turn_id)
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
