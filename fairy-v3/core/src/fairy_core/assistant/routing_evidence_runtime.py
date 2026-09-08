from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fairy_core.assistant.candidates import ToolCandidate
from fairy_core.assistant.evidence import EvidenceClassificationFailedError
from fairy_core.assistant.interpretation import (
    AssistantRequestInterpretationRevision,
    InterpretationDisposition,
    build_classifier_input_envelopes,
)
from fairy_core.assistant.models import AssistantTurn, MessageRole
from fairy_core.assistant.routing import (
    DEEPSEEK_MODEL_ID,
    EvidenceClassificationPayload,
    RoutingDecision,
    RoutingTaskKind,
    build_manual_evidence_request,
    merge_evidence_outputs,
    parse_evidence_classification,
    parse_router_output,
)
from fairy_core.assistant.turn_reader import require_turn
from fairy_core.commanding import CommandRun
from fairy_core.commanding.registry import SideEffect
from fairy_core.mcp.ports import McpCancelledError
from fairy_core.model_catalog.models import (
    MODEL_ALLOWLIST_BY_ID,
    ModelCatalogSnapshot,
    ModelEndpointKind,
    ModelSelectionMode,
    ProviderCredentialStatus,
    baseline_catalog,
)
from fairy_core.providers import (
    CancellationToken,
    ModelDelta,
    ModelDeltaKind,
    ModelExecutionRole,
    ProviderCancelledError,
    ProviderCapability,
    ProviderProtocolError,
)

_CLARIFICATION_CLASSIFIER_ROUND_BASE = 10_000


def route_step_summary(decision: RoutingDecision) -> str:
    task_label = {
        RoutingTaskKind.GENERAL: "General",
        RoutingTaskKind.REASONING: "Reasoning",
        RoutingTaskKind.CODE: "Code",
        RoutingTaskKind.BROWSER: "Browser QA",
        RoutingTaskKind.IMAGE: "Image",
        RoutingTaskKind.MUSIC: "Music",
        RoutingTaskKind.VIDEO: "Video",
    }[decision.task_kind]
    primary = MODEL_ALLOWLIST_BY_ID[decision.primary_model_id].display_name
    if decision.reviewer_model_id is None:
        return f"{task_label} task routed to {primary}"
    reviewer = MODEL_ALLOWLIST_BY_ID[decision.reviewer_model_id].display_name
    return f"{task_label} task routed to {primary}, reviewed by {reviewer}"


@dataclass(frozen=True, slots=True)
class RoutingClassifierInput:
    user_request: str
    source_message_id: UUID
    catalog: ModelCatalogSnapshot
    model_round: int
    interpretation_revision: int | None = None
    prior_interpretation: AssistantRequestInterpretationRevision | None = None
    mcp_action_targets: tuple[str, ...] = ()


def validate_router_attempt(deltas: tuple[ModelDelta, ...]) -> None:
    chunks: list[str] = []
    for delta in deltas:
        if delta.kind is ModelDeltaKind.TEXT:
            if delta.text is None:
                raise ProviderProtocolError("router returned an empty text delta")
            chunks.append(delta.text)
            if sum(map(len, chunks)) > 16_384:
                raise ProviderProtocolError("router response is too large")
        elif delta.kind is ModelDeltaKind.TOOL_CALL:
            raise ProviderProtocolError("router cannot call tools")
    try:
        parse_router_output("".join(chunks))
    except ValueError as error:
        raise ProviderProtocolError("router returned invalid structured output") from error


class EvidenceRoutingRuntimeMixin:
    def _run_manual_evidence_classifier(
        self,
        *,
        turn: AssistantTurn,
        classifier_input: RoutingClassifierInput,
        cancellation: CancellationToken,
    ) -> tuple[EvidenceClassificationPayload, CommandRun]:
        selection = turn.model_selection
        if selection is None or selection.mode is not ModelSelectionMode.MANUAL:
            raise ValueError("manual evidence classifier requires Manual selection")
        selected = MODEL_ALLOWLIST_BY_ID.get(selection.model_id or "")
        if selected is None:
            raise EvidenceClassificationFailedError("selected model cannot be interpreted")
        classifier_model_id = (
            selection.model_id
            if selected.endpoint_kind is ModelEndpointKind.CHAT
            else DEEPSEEK_MODEL_ID
        )
        profile = self._providers.profile_for_model(classifier_model_id or "")
        structured = ProviderCapability.STRUCTURED_OUTPUT in profile.capabilities
        if not structured and ProviderCapability.TOOLS not in profile.capabilities:
            raise EvidenceClassificationFailedError(
                "selected provider cannot classify evidence requirements"
            )
        _started_turn, run = self._start_model_round(
            turn.id,
            classifier_input.model_round,
            profile_id=profile.id,
            model_role=ModelExecutionRole.COORDINATOR,
        )
        try:
            attachment_count = len(self._image_attachments.for_turn(turn.id))
            envelopes = build_classifier_input_envelopes(
                source_message_id=classifier_input.source_message_id,
                content=classifier_input.user_request,
                attachment_count=attachment_count,
            )
            outputs: list[EvidenceClassificationPayload] = []
            for envelope in envelopes:
                request = build_manual_evidence_request(
                    profile_id=profile.id,
                    user_request=classifier_input.user_request,
                    source_message_id=classifier_input.source_message_id,
                    selection=selection,
                    use_structured_output=structured,
                    attachment_count=attachment_count,
                    prior_interpretation=classifier_input.prior_interpretation,
                    classifier_envelope=envelope,
                    mcp_action_targets=classifier_input.mcp_action_targets,
                )
                chunks: list[str] = []
                candidates: dict[str, ToolCandidate] = {}
                for delta in self._providers.stream(
                    request,
                    cancellation,
                    on_attempt=self._provider_attempts.observer(
                        turn.id,
                        classifier_input.model_round,
                        run=run,
                    ),
                ):
                    cancellation.raise_if_cancelled()
                    self._turns.require_active(turn.id)
                    if delta.kind is ModelDeltaKind.TEXT:
                        if delta.text is None:
                            raise EvidenceClassificationFailedError(
                                "evidence classifier returned empty text"
                            )
                        chunks.append(delta.text)
                        if sum(map(len, chunks)) > 16_384:
                            raise EvidenceClassificationFailedError(
                                "evidence classifier response is too large"
                            )
                    elif delta.kind is ModelDeltaKind.TOOL_CALL:
                        if delta.tool_call_id is None:
                            raise EvidenceClassificationFailedError(
                                "evidence classifier tool call has no id"
                            )
                        candidate = candidates.setdefault(
                            delta.tool_call_id,
                            ToolCandidate(call_id=delta.tool_call_id),
                        )
                        candidate.append(delta)
                try:
                    if structured:
                        if candidates:
                            raise ValueError("structured evidence classifier called a tool")
                        classified = parse_evidence_classification("".join(chunks))
                    else:
                        if len(candidates) != 1 or (chunks and "".join(chunks).strip()):
                            raise ValueError(
                                "tool evidence classifier returned an invalid response"
                            )
                        candidate = next(iter(candidates.values()))
                        if candidate.name != "evidence.classify":
                            raise ValueError("evidence classifier called an unknown tool")
                        classified = parse_evidence_classification(
                            json.dumps(
                                candidate.arguments(),
                                ensure_ascii=True,
                                separators=(",", ":"),
                                sort_keys=True,
                            )
                        )
                except ValueError as error:
                    raise EvidenceClassificationFailedError(
                        "evidence classifier returned invalid structured output"
                    ) from error
                outputs.append(classified)
            classified = merge_evidence_outputs(tuple(outputs))
            return classified, run
        except (ProviderCancelledError, McpCancelledError):
            if cancellation.is_interrupted:
                self._abandon_model_run(run)
            else:
                self._cancel_model_run(run)
            raise
        except EvidenceClassificationFailedError:
            self._fail_model_run(run, error_code="EVIDENCE_CLASSIFICATION_FAILED")
            raise
        except Exception:
            self._fail_model_run(run, error_code="PROVIDER_ROUTING_FAILED")
            raise

    def _routing_inputs(self, turn_id: UUID) -> RoutingClassifierInput:
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            interpretation = unit_of_work.assistant.get_interpretation(
                turn.id,
                turn.active_interpretation_revision,
            )
            refresh = interpretation is not None and (
                interpretation.disposition is InterpretationDisposition.CLARIFICATION_REQUIRED
                or interpretation.idempotency_key.startswith("steer:")
            )
            user_message = (
                unit_of_work.assistant.get_message(interpretation.source_message_id)
                if refresh and interpretation is not None
                else unit_of_work.assistant.message_for_turn(turn.id, MessageRole.USER)
            )
            if user_message is None:
                raise RuntimeError("Assistant Turn has no user message")
            catalog = unit_of_work.model_catalog.get_catalog()
        if catalog is None:
            catalog = baseline_catalog(
                now=datetime.now(UTC),
                credential_status=ProviderCredentialStatus.CONFIGURED,
            )
        return RoutingClassifierInput(
            user_request=user_message.content,
            source_message_id=user_message.id,
            catalog=catalog,
            interpretation_revision=turn.active_interpretation_revision,
            model_round=(
                _CLARIFICATION_CLASSIFIER_ROUND_BASE + interpretation.revision
                if refresh and interpretation
                else 1
            ),
            prior_interpretation=interpretation if refresh else None,
            mcp_action_targets=tuple(sorted(
                definition.name for definition in self._registry.definitions()
                if definition.source == "mcp" and definition.model_visible
                and definition.side_effect in {SideEffect.WRITE, SideEffect.EXECUTE}
            ))[:64],
        )


__all__ = [
    "EvidenceRoutingRuntimeMixin",
    "RoutingClassifierInput",
    "route_step_summary",
    "validate_router_attempt",
]
