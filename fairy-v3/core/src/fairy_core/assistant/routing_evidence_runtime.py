from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from fairy_core.assistant.candidates import ToolCandidate
from fairy_core.assistant.evidence import EvidenceClassificationFailedError
from fairy_core.assistant.models import AssistantTurn, MessageRole
from fairy_core.assistant.routing import (
    EvidenceClassificationPayload,
    RoutingDecision,
    RoutingTaskKind,
    build_manual_evidence_request,
    parse_evidence_classification,
    parse_router_output,
)
from fairy_core.assistant.turn_reader import require_turn
from fairy_core.commanding import CommandRun
from fairy_core.mcp.ports import McpCancelledError
from fairy_core.model_catalog.models import (
    MODEL_ALLOWLIST_BY_ID,
    ModelCatalogSnapshot,
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
        user_request: str,
        cancellation: CancellationToken,
    ) -> tuple[EvidenceClassificationPayload, CommandRun]:
        selection = turn.model_selection
        if selection is None or selection.mode is not ModelSelectionMode.MANUAL:
            raise ValueError("manual evidence classifier requires Manual selection")
        profile = self._providers.profile_for_model(selection.model_id or "")
        structured = ProviderCapability.STRUCTURED_OUTPUT in profile.capabilities
        if not structured and ProviderCapability.TOOLS not in profile.capabilities:
            raise EvidenceClassificationFailedError(
                "selected provider cannot classify evidence requirements"
            )
        _started_turn, run = self._start_model_round(
            turn.id,
            1,
            profile_id=profile.id,
            model_role=ModelExecutionRole.COORDINATOR,
        )
        try:
            request = build_manual_evidence_request(
                profile_id=profile.id,
                user_request=user_request,
                source_message_id=self._routing_source_message(turn.id).id,
                selection=selection,
                use_structured_output=structured,
            )
            chunks: list[str] = []
            candidates: dict[str, ToolCandidate] = {}
            for delta in self._providers.stream(
                request,
                cancellation,
                on_attempt=self._provider_attempts.observer(turn.id, 1, run=run),
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
                        raise ValueError("tool evidence classifier returned an invalid response")
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


__all__ = [
    "EvidenceRoutingRuntimeMixin",
    "route_step_summary",
    "validate_router_attempt",
]
