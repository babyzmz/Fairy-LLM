from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fairy_core.assistant.command_leases import assistant_command_lease_until
from fairy_core.assistant.events import append_message_created
from fairy_core.assistant.models import (
    AssistantTurn,
    AssistantTurnStatus,
    MessageRole,
    MessageVisibility,
)
from fairy_core.assistant.routing import RoutingDecision
from fairy_core.assistant.trace_models import TraceStepKind, TraceStepStatus
from fairy_core.assistant.turn_reader import require_task, require_turn
from fairy_core.commanding import CommandStatus, EventVisibility
from fairy_core.commanding.bus import CommandRequest
from fairy_core.domain.execution import Approval
from fairy_core.domain.models import TaskStatus
from fairy_core.providers import CancellationToken, ProviderUnavailableError


class RoutingBudgetRuntimeMixin:
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
                    idempotency_key=(
                        f"assistant:{turn.id}:model-budget:"
                        f"{turn.active_interpretation_revision or 0}"
                    ),
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
                if user_message is not None and user_message.visibility is MessageVisibility.USER:
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
            route_step = self._append_route_trace_in_unit(
                unit_of_work,
                turn=turn,
                run=run,
                decision=decision,
            )
            self._trace.append_step_in_unit(
                unit_of_work,
                turn=turn,
                run=run,
                kind=TraceStepKind.APPROVAL,
                status=TraceStepStatus.WAITING,
                public_summary="Waiting for model budget approval",
                caused_by_step_id=route_step.id,
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
                self._trace.transition_command_step_in_unit(
                    unit_of_work,
                    run=command,
                    kind=TraceStepKind.APPROVAL,
                    status=TraceStepStatus.CANCELLED,
                    public_summary="Model budget approval declined",
                )
                unit_of_work.commit()
                return "rejected"
            bus = self._command_bus(unit_of_work.commands)
            if command.status is CommandStatus.QUEUED:
                running = bus.start(
                    command.id,
                    lease_until=assistant_command_lease_until(),
                )
            elif command.status is CommandStatus.RUNNING:
                if command.lease_until is None or command.lease_until > datetime.now(UTC):
                    return "waiting"
                running = bus.start(
                    command.id,
                    lease_until=assistant_command_lease_until(),
                )
            elif command.status is CommandStatus.SUCCEEDED:
                running = None
            else:
                raise ProviderUnavailableError(
                    f"model budget approval ended as {command.status.value}"
                )

            if running is not None:
                self._trace.transition_command_step_in_unit(
                    unit_of_work,
                    run=running,
                    kind=TraceStepKind.APPROVAL,
                    status=TraceStepStatus.SUCCEEDED,
                    public_summary="Model budget approved",
                )
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


__all__ = ["RoutingBudgetRuntimeMixin"]
