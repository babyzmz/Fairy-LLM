from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fairy_core.assistant import limits
from fairy_core.assistant.command_leases import assistant_command_lease_until
from fairy_core.assistant.context import bound_persona_instruction
from fairy_core.assistant.events import append_message_created
from fairy_core.assistant.interpretation import (
    ClassifierInterpretationPayload,
    InterpretationDisposition,
    RequestAction,
    build_classifier_input_envelopes,
    fallback_interpretation,
    interpretation_from_classifier,
)
from fairy_core.assistant.models import (
    AssistantTurn,
    AssistantTurnStatus,
    MessageRole,
    MessageVisibility,
)
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
    merge_router_outputs,
    parse_router_output,
)
from fairy_core.assistant.routing_budget_runtime import RoutingBudgetRuntimeMixin
from fairy_core.assistant.routing_evidence_runtime import (
    EvidenceRoutingRuntimeMixin,
    RoutingClassifierInput,
    route_step_summary,
    validate_router_attempt,
)
from fairy_core.assistant.system_intent import unrouted_interpretation
from fairy_core.assistant.trace_models import TraceStepKind, TraceStepStatus
from fairy_core.assistant.turn_reader import require_task, require_turn
from fairy_core.commanding import CommandRun, CommandStatus, EventVisibility
from fairy_core.commanding.bus import CommandRequest
from fairy_core.domain.models import TaskStatus
from fairy_core.mcp.ports import McpCancelledError
from fairy_core.model_catalog.models import (
    MODEL_ALLOWLIST_BY_ID,
    ModelAvailability,
    ModelCatalogSnapshot,
    ModelSelectionMode,
    ModelSelectionSnapshot,
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
from fairy_core.workflow.scheduler import WorkflowPaused


class AssistantRoutingMixin(EvidenceRoutingRuntimeMixin, RoutingBudgetRuntimeMixin):
    def _ensure_unrouted_interpretation(self, turn_id: UUID) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            if turn.active_interpretation_revision is not None:
                return
            source_message = unit_of_work.assistant.message_for_turn(
                turn.id,
                MessageRole.USER,
            )
            if source_message is None:
                raise RuntimeError("Assistant Turn has no user message")
            interpretation = unrouted_interpretation(
                turn_id=turn.id,
                revision=1,
                source_message_id=source_message.id,
                source_message=source_message.content,
            )
            unit_of_work.assistant.append_interpretation(
                interpretation,
                expected_revision=None,
            )
            unit_of_work.commit()

    def _ensure_routing(
        self,
        turn: AssistantTurn,
        cancellation: CancellationToken,
    ) -> RoutingDecision | None:
        if turn.model_selection is None:
            return turn.routing_decision
        if turn.routing_decision is not None:
            if turn.routing_decision.evidence_classified:
                return turn.routing_decision
            classifier_input = self._routing_inputs(turn.id)
            evidence, route_run = self._run_manual_evidence_classifier(
                turn=turn,
                classifier_input=classifier_input,
                cancellation=cancellation,
            )
            decision = manual_routing_decision(
                selection=turn.model_selection,
                catalog=classifier_input.catalog,
                user_request=classifier_input.user_request,
                evidence=evidence,
            )
            self._bind_routing(
                turn.id,
                decision,
                run=route_run,
                classifier_input=classifier_input,
                replace_evidence=True,
                interpretation_payload=evidence.interpretation,
            )
            return decision
        classifier_input = self._routing_inputs(turn.id)
        if turn.model_selection.mode is ModelSelectionMode.MANUAL:
            preliminary = manual_routing_decision(
                selection=turn.model_selection,
                catalog=classifier_input.catalog,
                user_request=classifier_input.user_request,
            )
            self._require_available_route(preliminary, classifier_input.catalog)
            self._require_selection_compatibility(turn.model_selection, preliminary)
            evidence, route_run = self._run_manual_evidence_classifier(
                turn=turn,
                classifier_input=classifier_input,
                cancellation=cancellation,
            )
            decision = manual_routing_decision(
                selection=turn.model_selection,
                catalog=classifier_input.catalog,
                user_request=classifier_input.user_request,
                evidence=evidence,
            )
            self._require_available_route(decision, classifier_input.catalog)
            self._require_selection_compatibility(turn.model_selection, decision)
            self._bind_routing(
                turn.id,
                decision,
                run=route_run,
                classifier_input=classifier_input,
                interpretation_payload=(evidence.interpretation if evidence is not None else None),
            )
            return decision
        return self._run_auto_router(
            turn=turn,
            classifier_input=classifier_input,
            cancellation=cancellation,
        )

    def _run_auto_router(
        self,
        *,
        turn: AssistantTurn,
        classifier_input: RoutingClassifierInput,
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
            classifier_input.model_round,
            profile_id=primary.id,
            model_role=ModelExecutionRole.COORDINATOR,
        )
        try:
            attachment_count = len(self._image_attachments.for_turn(turn.id))
            envelopes = build_classifier_input_envelopes(
                source_message_id=classifier_input.source_message_id,
                content=classifier_input.user_request,
                attachment_count=attachment_count,
            )
            routed_outputs = []
            for envelope in envelopes:
                request = build_router_request(
                    profile_id=primary.id,
                    user_request=classifier_input.user_request,
                    source_message_id=classifier_input.source_message_id,
                    attachment_count=attachment_count,
                    selection=selection,
                    fallback_profile_ids=fallback_ids,
                    prior_interpretation=classifier_input.prior_interpretation,
                    classifier_envelope=envelope,
                )
                chunks: list[str] = []
                for delta in self._providers.stream(
                    request,
                    cancellation,
                    on_attempt=self._provider_attempts.observer(
                        turn.id,
                        classifier_input.model_round,
                        run=run,
                    ),
                    attempt_validator=validate_router_attempt,
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
                routed_outputs.append(parse_router_output("".join(chunks)))
            routed = merge_router_outputs(tuple(routed_outputs))
            decision = auto_routing_decision(
                routed=routed,
                catalog=classifier_input.catalog,
                user_request=classifier_input.user_request,
                attachment_count=attachment_count,
                allow_free_fallback=selection.allow_free_fallback,
            )
            self._require_available_route(decision, classifier_input.catalog)
            self._require_selection_compatibility(selection, decision)
            self._bind_routing(
                turn.id,
                decision,
                run=run,
                classifier_input=classifier_input,
                interpretation_payload=routed.interpretation,
            )
            return decision
        except (ProviderCancelledError, McpCancelledError):
            if cancellation.is_interrupted:
                self._abandon_model_run(run)
            else:
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
        classifier_input: RoutingClassifierInput,
        run: CommandRun | None = None,
        replace_evidence: bool = False,
        interpretation_payload: ClassifierInterpretationPayload | None = None,
    ) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            if turn.active_interpretation_revision != classifier_input.interpretation_revision:
                # Fence the result within the same transaction as interpretation binding.
                # A delayed classifier may never authorize a newer source message.
                if run is not None:
                    self._trace.transition_command_step_in_unit(
                        unit_of_work,
                        run=run,
                        kind=TraceStepKind.MODEL,
                        status=TraceStepStatus.FAILED,
                        public_detail="Requirements changed while the request was analyzed.",
                    )
                    self._command_bus(unit_of_work.commands).fail(
                        run.id,
                        error_code="EXECUTION_INTENT_CHANGED",
                        lease_owner=run.lease_owner,
                        lease_fence=run.lease_fence,
                    )
                    unit_of_work.commit()
                raise WorkflowPaused
            expected_status = turn.status
            expected_revision = turn.cancellation_revision
            current_interpretation = unit_of_work.assistant.get_interpretation(
                turn.id,
                turn.active_interpretation_revision,
            )
            should_append_interpretation = (
                current_interpretation is None
                and (decision.evidence_classified or interpretation_payload is not None)
            ) or (
                current_interpretation is not None
                and (
                    current_interpretation.disposition
                    is InterpretationDisposition.CLARIFICATION_REQUIRED
                    or current_interpretation.idempotency_key.startswith("steer:")
                )
                and interpretation_payload is not None
            )
            if should_append_interpretation:
                source_message = (
                    unit_of_work.assistant.get_message(current_interpretation.source_message_id)
                    if current_interpretation is not None
                    else unit_of_work.assistant.message_for_turn(
                        turn.id,
                        MessageRole.USER,
                    )
                )
                if source_message is None:
                    raise RuntimeError("Assistant Turn has no user message")
                action = _request_action(decision)
                next_interpretation_revision = (
                    current_interpretation.revision + 1 if current_interpretation is not None else 1
                )
                interpretation = (
                    interpretation_from_classifier(
                        turn_id=turn.id,
                        revision=next_interpretation_revision,
                        source_message_id=source_message.id,
                        source_message=source_message.content,
                        payload=interpretation_payload,
                        evidence_requirements=decision.evidence_requirements,
                    )
                    if interpretation_payload is not None
                    else fallback_interpretation(
                        turn_id=turn.id,
                        revision=next_interpretation_revision,
                        source_message_id=source_message.id,
                        source_message=source_message.content,
                        action=action,
                        evidence_requirements=decision.evidence_requirements,
                    )
                )
                unit_of_work.assistant.append_interpretation(
                    interpretation,
                    expected_revision=(
                        current_interpretation.revision
                        if current_interpretation is not None
                        else None
                    ),
                )
                turn.bind_interpretation(
                    next_interpretation_revision,
                    expected_revision=(
                        current_interpretation.revision
                        if current_interpretation is not None
                        else None
                    ),
                )
            if replace_evidence:
                turn.bind_routing_evidence(decision)
            else:
                turn.bind_routing(decision)
            unit_of_work.assistant.update_turn(
                turn,
                expected_status=expected_status,
                expected_cancellation_revision=expected_revision,
            )
            if run is not None:
                model_step = self._trace.transition_command_step_in_unit(
                    unit_of_work,
                    run=run,
                    kind=TraceStepKind.MODEL,
                    status=TraceStepStatus.SUCCEEDED,
                    public_summary="Request analyzed",
                )
                self._append_route_trace_in_unit(
                    unit_of_work,
                    turn=turn,
                    run=run,
                    decision=decision,
                    caused_by_step_id=model_step.id if model_step is not None else None,
                )
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

    def _routing_source_message(self, turn_id: UUID):
        with self._unit_of_work_factory() as unit_of_work:
            message = unit_of_work.assistant.message_for_turn(turn_id, MessageRole.USER)
        if message is None:
            raise RuntimeError("Assistant Turn has no user message")
        return message

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

    @staticmethod
    def _require_selection_compatibility(
        selection: ModelSelectionSnapshot,
        decision: RoutingDecision,
    ) -> None:
        if selection.zero_data_retention and decision.task_kind is RoutingTaskKind.VIDEO:
            raise ProviderUnavailableError(
                "Video generation is unavailable while zero data retention is enabled"
            )

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
            self._trace.transition_command_step_in_unit(
                unit_of_work,
                run=persisted,
                kind=TraceStepKind.MODEL,
                status=TraceStepStatus.SUCCEEDED,
                public_summary="Model work completed",
            )
            self._command_bus(unit_of_work.commands).complete(
                run.id,
                output=output,
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
            )
            unit_of_work.commit()

    def _reject_model_round_for_retry(
        self,
        run: CommandRun,
        *,
        error_code: str,
        public_summary: str = "Tool request could not be validated",
        public_detail: str,
    ) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            persisted = unit_of_work.commands.get_run(run.id)
            if persisted is None:
                raise RuntimeError("model CommandRun is missing")
            if persisted.status is not CommandStatus.RUNNING:
                raise RuntimeError("model CommandRun is not running")
            self._trace.transition_command_step_in_unit(
                unit_of_work,
                run=persisted,
                kind=TraceStepKind.MODEL,
                status=TraceStepStatus.FAILED,
                public_summary=public_summary,
                public_detail=public_detail,
            )
            self._command_bus(unit_of_work.commands).fail(
                run.id,
                error_code=error_code,
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
        cited_evidence_receipt_ids: tuple[str, ...] = (),
    ) -> AssistantTurn:
        if decision.reviewer_model_id is None:
            raise ValueError("reviewer model is not configured")
        turn = self._turns.get(turn_id)
        if turn.harness_manifest_id is None or turn.harness_manifest_hash is None:
            raise ValueError("reviewer requires a bound Harness Manifest")
        with self._unit_of_work_factory() as unit_of_work:
            manifest = unit_of_work.knowledge.get_manifest(
                turn.harness_manifest_id,
                task_id=turn.task_id,
            )
        if manifest is None or manifest.content_hash != turn.harness_manifest_hash:
            raise ValueError("reviewer Harness Manifest binding is unavailable")
        persona_instruction = bound_persona_instruction(manifest)
        persona_block = f"{persona_instruction}\n\n" if persona_instruction else ""
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
                        f"{persona_block}"
                        "Review the proposed response for correctness, completeness, and safety. "
                        "Return only the polished final answer. Do not describe the review, expose "
                        "hidden reasoning, or call tools. Preserve the bound Fairy identity and "
                        "voice. Never identify yourself as the provider or underlying model. "
                        "For generated Workspace files, return a "
                        "concise summary and never repeat full file contents or invent a localhost "
                        "URL or port."
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
                on_attempt=self._provider_attempts.observer(turn_id, model_round, run=run),
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
                cited_evidence_receipt_ids=cited_evidence_receipt_ids,
            )
        except ProviderCancelledError:
            if cancellation.is_interrupted:
                self._abandon_model_run(run)
                raise
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
        maximum_characters = 24_000
        maximum_message_characters = 8_000
        selected: list[ModelMessage] = []
        remaining = maximum_characters
        for message in reversed(messages):
            if message.role not in {ModelRole.USER, ModelRole.ASSISTANT}:
                continue
            if message.tool_calls:
                continue
            content = message.content or (
                "The original request included image attachments." if message.images else ""
            )
            if not content:
                continue
            content = content[-maximum_message_characters:]
            if len(content) > remaining and selected:
                break
            content = content[-max(1, remaining) :]
            selected.append(ModelMessage.create(role=message.role, content=content))
            remaining -= len(content)
            if remaining <= 0:
                break
        selected.reverse()
        return tuple(selected)

    def _reset_message_projection(
        self,
        *,
        turn_id: UUID,
        run: CommandRun,
        through_chunk_index: int,
        reason: str = "provider_protocol_error",
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
                    "reason": reason,
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
                "public_summary": route_step_summary(decision),
            },
            lease_owner=(run.lease_owner if run.status is CommandStatus.RUNNING else None),
            lease_fence=(run.lease_fence if run.status is CommandStatus.RUNNING else None),
        )

    def _fail_model_run(self, run: CommandRun, *, error_code: str) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            persisted = unit_of_work.commands.get_run(run.id)
            if persisted is not None and persisted.status is CommandStatus.RUNNING:
                self._trace.transition_command_step_in_unit(
                    unit_of_work,
                    run=persisted,
                    kind=TraceStepKind.MODEL,
                    status=TraceStepStatus.FAILED,
                    public_detail=f"Model work failed ({error_code}).",
                )
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
                self._trace.transition_command_step_in_unit(
                    unit_of_work,
                    run=persisted,
                    kind=TraceStepKind.MODEL,
                    status=TraceStepStatus.CANCELLED,
                    public_summary="Model work cancelled",
                )
                self._command_bus(unit_of_work.commands).cancel(
                    run.id,
                    lease_owner=run.lease_owner,
                    lease_fence=run.lease_fence,
                )
                unit_of_work.commit()

    def _abandon_model_run(self, run: CommandRun) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            abandoned = unit_of_work.commands.abandon(
                run.id,
                lease_owner=run.lease_owner or "",
                lease_fence=run.lease_fence,
            )
            if abandoned:
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
            reclaimed = dispatch.run.status is CommandStatus.RUNNING
            if dispatch.run.status is CommandStatus.QUEUED:
                self._reserve_workflow_budget_in_unit(
                    unit_of_work,
                    turn,
                    model_rounds=1,
                )
                consume_model_budget(unit_of_work, task.id)
                running = bus.start(
                    dispatch.run.id,
                    lease_until=assistant_command_lease_until(),
                )
            elif (
                reclaimed
                and dispatch.run.lease_until is not None
                and dispatch.run.lease_until <= datetime.now(UTC)
            ):
                running = bus.start(
                    dispatch.run.id,
                    lease_until=assistant_command_lease_until(),
                )
                unit_of_work.commands.append_event(
                    run_id=running.id,
                    event_type="assistant.message.projection_reset",
                    visibility=EventVisibility.USER,
                    message="Interrupted assistant response cleared",
                    payload={
                        "turn_id": str(turn.id),
                        "through_chunk_index": 0,
                        "reason": "worker_recovery",
                    },
                    lease_owner=running.lease_owner,
                    lease_fence=running.lease_fence,
                )
            else:
                raise RuntimeError("model generation command is not queued")
            if started:
                user_message = unit_of_work.assistant.message_for_turn(
                    turn.id,
                    MessageRole.USER,
                )
                if user_message is not None and user_message.visibility is MessageVisibility.USER:
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
            route_step = None
            if turn.routing_decision is not None:
                route_step = self._append_route_trace_in_unit(
                    unit_of_work,
                    turn=turn,
                    run=running,
                    decision=turn.routing_decision,
                )
            model_cause = self._model_cause_step(
                unit_of_work,
                turn_id=turn.id,
                model_role=model_role,
                route_step=route_step,
            )
            profile = self._providers.profile(selected_profile_id)
            existing_model_step = unit_of_work.assistant.find_trace_step_by_command_run_id(
                running.id,
                kind=TraceStepKind.MODEL,
            )
            if not reclaimed or existing_model_step is None or existing_model_step.is_terminal:
                self._trace.append_step_in_unit(
                    unit_of_work,
                    turn=turn,
                    run=running,
                    kind=TraceStepKind.MODEL,
                    status=TraceStepStatus.RUNNING,
                    public_summary=self._model_step_summary(model_role),
                    caused_by_step_id=model_cause.id if model_cause is not None else None,
                    model_id=profile.model_id,
                    model_role=model_role,
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
            self._trace.transition_command_step_in_unit(
                unit_of_work,
                run=run,
                kind=TraceStepKind.MODEL,
                status=TraceStepStatus.SUCCEEDED,
                public_summary="Tool request prepared",
            )
            self._command_bus(unit_of_work.commands).complete(
                run.id,
                output={"tool_candidates": candidate_count},
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
            )
            unit_of_work.commit()

    def _append_route_trace_in_unit(
        self,
        unit_of_work,
        *,
        turn: AssistantTurn,
        run: CommandRun,
        decision: RoutingDecision,
        caused_by_step_id: UUID | None = None,
    ):
        existing = next(
            (
                step
                for step in unit_of_work.assistant.list_trace_steps(turn.id)
                if step.kind is TraceStepKind.ROUTE
            ),
            None,
        )
        if existing is not None:
            return existing
        return self._trace.append_step_in_unit(
            unit_of_work,
            turn=turn,
            run=run,
            kind=TraceStepKind.ROUTE,
            status=TraceStepStatus.SUCCEEDED,
            public_summary=route_step_summary(decision),
            caused_by_step_id=caused_by_step_id,
        )

    @staticmethod
    def _model_step_summary(model_role: ModelExecutionRole) -> str:
        return {
            ModelExecutionRole.COORDINATOR: "Analyzing the request",
            ModelExecutionRole.PRIMARY: "Working on the request",
            ModelExecutionRole.REVIEWER: "Reviewing the response",
        }[model_role]

    @staticmethod
    def _model_cause_step(
        unit_of_work,
        *,
        turn_id: UUID,
        model_role: ModelExecutionRole,
        route_step,
    ):
        steps = unit_of_work.assistant.list_trace_steps(turn_id)
        if model_role is ModelExecutionRole.REVIEWER:
            return next(
                (
                    step
                    for step in reversed(steps)
                    if step.kind is TraceStepKind.MODEL
                    and step.model_role is ModelExecutionRole.PRIMARY
                ),
                route_step,
            )
        if model_role is ModelExecutionRole.PRIMARY:
            return next(
                (step for step in reversed(steps) if step.kind is TraceStepKind.OBSERVATION),
                route_step,
            )
        return None


def _request_action(decision: RoutingDecision) -> RequestAction:
    if decision.task_kind is RoutingTaskKind.BROWSER:
        return RequestAction.BROWSE
    if decision.task_kind in {
        RoutingTaskKind.IMAGE,
        RoutingTaskKind.MUSIC,
        RoutingTaskKind.VIDEO,
    }:
        return RequestAction.GENERATE
    if decision.requires_workspace_changes:
        return RequestAction.CHANGE
    return RequestAction.ANSWER
