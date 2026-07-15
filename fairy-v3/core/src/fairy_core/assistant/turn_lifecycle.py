from __future__ import annotations

from uuid import UUID

from fairy_core.assistant.events import append_message_created
from fairy_core.assistant.models import (
    AssistantTurn,
    AssistantTurnStatus,
    Message,
    MessageRole,
    MessageVisibility,
)
from fairy_core.assistant.trace_models import TraceStepKind, TraceStepStatus
from fairy_core.assistant.turn_reader import require_task, require_turn
from fairy_core.commanding import CommandRun, CommandStatus, EventVisibility
from fairy_core.commanding.bus import CommandBus
from fairy_core.domain.models import TaskStatus
from fairy_core.providers import ModelExecutionRole


class AssistantTurnLifecycleMixin:
    def _complete_turn(
        self,
        *,
        turn_id: UUID,
        run: CommandRun,
        content: str,
        usage: dict[str, int],
    ) -> AssistantTurn:
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            task = require_task(unit_of_work, turn.task_id)
            completed_usage = usage
            if turn.model_selection is not None:
                attempt_usage: dict[str, int] = {}
                for attempt in unit_of_work.assistant.list_provider_attempts(turn.id):
                    for name, value in attempt.usage.items():
                        attempt_usage[name] = attempt_usage.get(name, 0) + value
                if attempt_usage:
                    completed_usage = attempt_usage
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
            turn.complete(usage=completed_usage)
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
            model_step = self._trace.transition_command_step_in_unit(
                unit_of_work,
                run=run,
                kind=TraceStepKind.MODEL,
                status=TraceStepStatus.SUCCEEDED,
                public_summary="Model response ready",
            )
            response_cause = model_step.id if model_step is not None else None
            if model_step is not None and model_step.model_role is ModelExecutionRole.REVIEWER:
                verification = self._trace.append_step_in_unit(
                    unit_of_work,
                    turn=turn,
                    run=run,
                    kind=TraceStepKind.VERIFICATION,
                    status=TraceStepStatus.SUCCEEDED,
                    public_summary="Response reviewed",
                    parent_step_id=model_step.id,
                    caused_by_step_id=model_step.id,
                )
                response_cause = verification.id
            self._trace.append_step_in_unit(
                unit_of_work,
                turn=turn,
                run=run,
                kind=TraceStepKind.RESPONSE,
                status=TraceStepStatus.SUCCEEDED,
                public_summary="Response ready",
                caused_by_step_id=response_cause,
            )
            self._trace.complete_trace_in_unit(unit_of_work, turn_id=turn.id)
            append_message_created(unit_of_work.commands, run=run, message=message)
            unit_of_work.commands.append_event(
                run_id=run.id,
                event_type="assistant.turn.completed",
                visibility=EventVisibility.USER,
                message="Assistant turn completed",
                payload={
                    "turn_id": str(turn.id),
                    "message_id": str(message.id),
                    "usage": completed_usage,
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
            turn = require_turn(unit_of_work, turn_id)
            if turn.status is not AssistantTurnStatus.CANCELLED:
                expected_status = turn.status
                expected_revision = turn.cancellation_revision
                turn.cancel()
                unit_of_work.assistant.update_turn(
                    turn,
                    expected_status=expected_status,
                    expected_cancellation_revision=expected_revision,
                )
            self._trace.finish_active_steps_in_unit(
                unit_of_work,
                turn_id=turn.id,
                run=run,
                status=TraceStepStatus.CANCELLED,
                public_detail="Turn cancelled.",
            )
            self._trace.complete_trace_in_unit(unit_of_work, turn_id=turn.id)
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
            task = require_task(unit_of_work, turn.task_id)
            if task.status in _ACTIVE_TASK_STATUSES:
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
            turn = require_turn(unit_of_work, turn_id)
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
            self._trace.finish_active_steps_in_unit(
                unit_of_work,
                turn_id=turn.id,
                run=run,
                status=TraceStepStatus.FAILED,
                public_detail=f"Turn failed ({error_code}).",
            )
            self._trace.complete_trace_in_unit(unit_of_work, turn_id=turn.id)
            if run is not None:
                persisted_run = unit_of_work.commands.get_run(run.id)
                if persisted_run is not None and persisted_run.status is CommandStatus.RUNNING:
                    unit_of_work.commands.append_event(
                        run_id=run.id,
                        event_type="assistant.turn.failed",
                        visibility=EventVisibility.USER,
                        message="Assistant turn failed",
                        payload={"turn_id": str(turn.id), "error_code": error_code},
                        lease_owner=run.lease_owner,
                        lease_fence=run.lease_fence,
                    )
                    self._command_bus(unit_of_work.commands).fail(
                        run.id,
                        error_code=error_code,
                        lease_owner=run.lease_owner,
                        lease_fence=run.lease_fence,
                    )
            task = require_task(unit_of_work, turn.task_id)
            if task.status in _ACTIVE_TASK_STATUSES:
                task.transition_to(TaskStatus.FAILED)
                unit_of_work.state.save_task(task)
            unit_of_work.commit()
        return turn

    def _command_bus(self, ledger) -> CommandBus:
        return CommandBus(
            registry=self._registry,
            policy=self._policy,
            ledger=ledger,
        )


_ACTIVE_TASK_STATUSES = frozenset(
    {
        TaskStatus.PLANNING,
        TaskStatus.AWAITING_APPROVAL,
        TaskStatus.EXECUTING,
        TaskStatus.INSTALLING,
        TaskStatus.PREVIEWING,
        TaskStatus.REVIEWING,
        TaskStatus.REPAIRING,
    }
)


__all__ = ["AssistantTurnLifecycleMixin"]
