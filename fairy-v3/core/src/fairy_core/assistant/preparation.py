from __future__ import annotations

from uuid import UUID

from fairy_core.assistant.evidence import EvidenceClassificationFailedError
from fairy_core.assistant.interpretation import InterpretationDisposition
from fairy_core.assistant.models import AssistantTurn, AssistantTurnStatus
from fairy_core.assistant.turn_reader import require_turn
from fairy_core.commanding import EventVisibility
from fairy_core.mcp.ports import McpCancelledError
from fairy_core.providers import (
    CancellationToken,
    ProviderAuthenticationError,
    ProviderCancelledError,
    ProviderContentRejectedError,
    ProviderContextLengthError,
    ProviderError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class AssistantPreparationMixin:
    def prepare_turn(
        self,
        turn_id: UUID,
        cancellation: CancellationToken,
        *,
        allow_budget_approval: bool = True,
    ) -> AssistantTurn:
        try:
            return self._prepare_turn(
                turn_id,
                cancellation,
                allow_budget_approval=allow_budget_approval,
            )
        except (ProviderCancelledError, McpCancelledError):
            return self._cancel_turn(turn_id, None)
        except ProviderAuthenticationError:
            return self._fail_turn(turn_id, None, error_code="PROVIDER_AUTHENTICATION_FAILED")
        except ProviderRateLimitError:
            return self._fail_turn(turn_id, None, error_code="PROVIDER_RATE_LIMITED")
        except ProviderTimeoutError:
            return self._fail_turn(turn_id, None, error_code="PROVIDER_TIMEOUT")
        except ProviderContextLengthError:
            return self._fail_turn(turn_id, None, error_code="PROVIDER_CONTEXT_LENGTH_EXCEEDED")
        except ProviderContentRejectedError:
            return self._fail_turn(turn_id, None, error_code="PROVIDER_CONTENT_REJECTED")
        except ProviderNetworkError:
            return self._fail_turn(turn_id, None, error_code="PROVIDER_NETWORK_ERROR")
        except ProviderUnavailableError as error:
            return self._fail_turn(turn_id, None, error_code=error.public_code)
        except EvidenceClassificationFailedError:
            return self._fail_turn(turn_id, None, error_code="EVIDENCE_CLASSIFICATION_FAILED")
        except ProviderError:
            return self._fail_turn(turn_id, None, error_code="PROVIDER_ERROR")
        except ValueError:
            return self._fail_turn(turn_id, None, error_code="PROVIDER_PROTOCOL_ERROR")

    def _prepare_turn(
        self,
        turn_id: UUID,
        cancellation: CancellationToken,
        *,
        allow_budget_approval: bool,
    ) -> AssistantTurn:
        """Persist interpretation and routing before execution becomes claimable."""

        turn = self._turns.get(turn_id)
        if turn.is_terminal:
            return turn
        if self._turns.submission_cancelled(turn):
            return self._cancel_turn(turn_id, None)
        if (
            turn.status is AssistantTurnStatus.WAITING_FOR_TOOL
            and turn.budget_approval_run_id is not None
        ):
            budget_state = self._resume_budget_approval(turn_id, cancellation)
            if budget_state == "waiting":
                return self._turns.get(turn_id)
            if budget_state == "rejected":
                return self._fail_turn(turn_id, None, error_code="USER_REJECTED")
            turn = self._turns.get(turn_id)
        decision = self._ensure_routing(turn, cancellation)
        if decision is None:
            self._ensure_unrouted_interpretation(turn_id)
        waiting_for_input = False
        with self._unit_of_work_factory() as unit_of_work:
            prepared = require_turn(unit_of_work, turn_id)
            interpretation = unit_of_work.assistant.get_interpretation(
                turn_id,
                prepared.active_interpretation_revision,
            )
            if (
                interpretation is not None
                and interpretation.disposition
                is InterpretationDisposition.CLARIFICATION_REQUIRED
                and prepared.status is not AssistantTurnStatus.WAITING_FOR_INPUT
            ):
                expected_status = prepared.status
                expected_revision = prepared.cancellation_revision
                prepared.wait_for_input()
                unit_of_work.assistant.update_turn(
                    prepared,
                    expected_status=expected_status,
                    expected_cancellation_revision=expected_revision,
                )
                unit_of_work.commands.append_domain_event(
                    event_type="assistant.turn.clarification_requested",
                    visibility=EventVisibility.USER,
                    message="Fairy needs clarification",
                    payload={
                        "turn_id": str(prepared.id),
                        "interpretation_revision": interpretation.revision,
                        "public_summary": interpretation.public_summary,
                    },
                    actor="assistant",
                    conversation_id=prepared.conversation_id,
                    task_id=prepared.task_id,
                )
                unit_of_work.commit()
                waiting_for_input = True
        if waiting_for_input:
            return self._turns.get(turn_id)
        turn = self._turns.get(turn_id)
        if (
            allow_budget_approval
            and decision is not None
            and decision.approval_required
            and turn.budget_approval_run_id is None
        ):
            return self._request_budget_approval(turn_id, decision)
        return turn


__all__ = ["AssistantPreparationMixin"]
