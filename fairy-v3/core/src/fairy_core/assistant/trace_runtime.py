from __future__ import annotations

from uuid import UUID

from fairy_core.assistant.models import AssistantTurn
from fairy_core.assistant.trace_models import (
    TraceStep,
    TraceStepKind,
    TraceStepStatus,
    TraceVisibility,
    TurnTrace,
)
from fairy_core.commanding import CommandRun, CommandStatus, EventVisibility
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import ModelExecutionRole


class TurnTraceRuntime:
    def __init__(self, unit_of_work_factory: CoreUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def append_step(
        self,
        *,
        turn_id: UUID,
        run: CommandRun,
        kind: TraceStepKind,
        status: TraceStepStatus,
        public_summary: str,
        visibility: TraceVisibility = TraceVisibility.USER,
        parent_step_id: UUID | None = None,
        caused_by_step_id: UUID | None = None,
        public_detail: str | None = None,
        model_id: str | None = None,
        model_role: ModelExecutionRole | None = None,
        artifact_refs: tuple[UUID, ...] = (),
    ) -> TraceStep:
        with self._unit_of_work_factory() as unit_of_work:
            turn = unit_of_work.assistant.get_turn(turn_id)
            if turn is None:
                raise KeyError(f"Assistant Turn not found: {turn_id}")
            step = self.append_step_in_unit(
                unit_of_work,
                turn=turn,
                run=run,
                kind=kind,
                status=status,
                public_summary=public_summary,
                visibility=visibility,
                parent_step_id=parent_step_id,
                caused_by_step_id=caused_by_step_id,
                public_detail=public_detail,
                model_id=model_id,
                model_role=model_role,
                artifact_refs=artifact_refs,
            )
            unit_of_work.commit()
        return step

    def transition_step(
        self,
        step_id: UUID,
        *,
        run: CommandRun,
        status: TraceStepStatus,
        public_summary: str | None = None,
        public_detail: str | None = None,
        artifact_refs: tuple[UUID, ...] | None = None,
    ) -> TraceStep:
        with self._unit_of_work_factory() as unit_of_work:
            step = self.transition_step_in_unit(
                unit_of_work,
                step_id=step_id,
                run=run,
                status=status,
                public_summary=public_summary,
                public_detail=public_detail,
                artifact_refs=artifact_refs,
            )
            unit_of_work.commit()
        return step

    def bind_provider_attempt(self, step_id: UUID, attempt_id: UUID) -> TraceStep:
        with self._unit_of_work_factory() as unit_of_work:
            step = unit_of_work.assistant.get_trace_step(step_id)
            if step is None:
                raise KeyError(f"Trace Step not found: {step_id}")
            expected_revision = step.revision
            step.bind_provider_attempt(attempt_id)
            unit_of_work.assistant.update_trace_step(
                step,
                expected_revision=expected_revision,
            )
            unit_of_work.commit()
        return step

    def bind_provider_attempt_in_unit(
        self,
        unit_of_work,
        *,
        step_id: UUID,
        run: CommandRun,
        attempt_id: UUID,
    ) -> TraceStep:
        step = unit_of_work.assistant.get_trace_step(step_id)
        if step is None:
            raise KeyError(f"Trace Step not found: {step_id}")
        if step.command_run_id != run.id:
            raise InvalidTransitionError("Trace Step CommandRun changed")
        expected_revision = step.revision
        step.bind_provider_attempt(attempt_id)
        unit_of_work.assistant.update_trace_step(
            step,
            expected_revision=expected_revision,
        )
        self._append_step_event(
            unit_of_work,
            run=run,
            step=step,
            event_type="updated",
        )
        return step

    def append_step_in_unit(
        self,
        unit_of_work,
        *,
        turn: AssistantTurn,
        run: CommandRun,
        kind: TraceStepKind,
        status: TraceStepStatus,
        public_summary: str,
        visibility: TraceVisibility = TraceVisibility.USER,
        parent_step_id: UUID | None = None,
        caused_by_step_id: UUID | None = None,
        public_detail: str | None = None,
        model_id: str | None = None,
        model_role: ModelExecutionRole | None = None,
        artifact_refs: tuple[UUID, ...] = (),
    ) -> TraceStep:
        trace = self._ensure_started(unit_of_work, turn=turn, run=run)
        self._require_related_step(unit_of_work, trace, parent_step_id)
        self._require_related_step(unit_of_work, trace, caused_by_step_id)
        step = TraceStep.create(
            trace_id=trace.id,
            turn_id=turn.id,
            sequence=unit_of_work.assistant.allocate_trace_sequence(trace.id),
            kind=kind,
            status=status,
            public_summary=public_summary,
            visibility=visibility,
            parent_step_id=parent_step_id,
            caused_by_step_id=caused_by_step_id,
            public_detail=public_detail,
            model_id=model_id,
            model_role=model_role,
            command_run_id=run.id,
            artifact_refs=artifact_refs,
        )
        unit_of_work.assistant.append_trace_step(step)
        self._append_step_event(unit_of_work, run=run, step=step, event_type="created")
        return step

    def transition_step_in_unit(
        self,
        unit_of_work,
        *,
        step_id: UUID,
        run: CommandRun,
        status: TraceStepStatus,
        public_summary: str | None = None,
        public_detail: str | None = None,
        artifact_refs: tuple[UUID, ...] | None = None,
    ) -> TraceStep:
        step = unit_of_work.assistant.get_trace_step(step_id)
        if step is None:
            raise KeyError(f"Trace Step not found: {step_id}")
        if step.command_run_id != run.id:
            raise InvalidTransitionError("Trace Step CommandRun changed")
        expected_revision = step.revision
        step.transition(
            status,
            public_summary=public_summary,
            public_detail=public_detail,
            artifact_refs=artifact_refs,
        )
        if step.revision == expected_revision:
            return step
        unit_of_work.assistant.update_trace_step(step, expected_revision=expected_revision)
        event_type = {
            TraceStepStatus.SUCCEEDED: "completed",
            TraceStepStatus.FAILED: "failed",
        }.get(step.status, "updated")
        self._append_step_event(unit_of_work, run=run, step=step, event_type=event_type)
        return step

    def transition_command_step_in_unit(
        self,
        unit_of_work,
        *,
        run: CommandRun,
        kind: TraceStepKind,
        status: TraceStepStatus,
        public_summary: str | None = None,
        public_detail: str | None = None,
        artifact_refs: tuple[UUID, ...] | None = None,
    ) -> TraceStep | None:
        step = unit_of_work.assistant.find_trace_step_by_command_run_id(
            run.id,
            kind=kind,
        )
        if step is None or step.is_terminal:
            return step
        return self.transition_step_in_unit(
            unit_of_work,
            step_id=step.id,
            run=run,
            status=status,
            public_summary=public_summary,
            public_detail=public_detail,
            artifact_refs=artifact_refs,
        )

    def finish_active_steps_in_unit(
        self,
        unit_of_work,
        *,
        turn_id: UUID,
        run: CommandRun | None,
        status: TraceStepStatus,
        public_detail: str,
        emit_events: bool = True,
    ) -> None:
        for step in unit_of_work.assistant.list_trace_steps(turn_id):
            if step.is_terminal:
                continue
            expected_revision = step.revision
            step.transition(status, public_detail=public_detail)
            unit_of_work.assistant.update_trace_step(
                step,
                expected_revision=expected_revision,
            )
            if not emit_events:
                continue
            event_run = run if run is not None and step.command_run_id == run.id else None
            if event_run is None and step.command_run_id is not None:
                event_run = unit_of_work.commands.get_run(step.command_run_id)
            if event_run is not None:
                event_type = "failed" if status is TraceStepStatus.FAILED else "updated"
                self._append_step_event(
                    unit_of_work,
                    run=event_run,
                    step=step,
                    event_type=event_type,
                )

    def complete_trace_in_unit(
        self,
        unit_of_work,
        *,
        turn_id: UUID,
    ) -> TurnTrace:
        trace = unit_of_work.assistant.get_trace_by_turn_id(turn_id)
        if trace is None:
            raise KeyError(f"Turn Trace not found: {turn_id}")
        expected_revision = trace.revision
        trace.complete()
        if trace.revision != expected_revision:
            unit_of_work.assistant.update_trace(trace, expected_revision=expected_revision)
        return trace

    def _ensure_started(self, unit_of_work, *, turn: AssistantTurn, run: CommandRun) -> TurnTrace:
        trace = unit_of_work.assistant.get_trace_by_turn_id(turn.id)
        if trace is None:
            trace, _inserted = unit_of_work.assistant.create_trace_if_absent(
                TurnTrace.create(
                    turn_id=turn.id,
                    conversation_id=turn.conversation_id,
                    task_id=turn.task_id,
                    legacy=True,
                )
            )
        if trace.started_at is not None:
            return trace
        expected_revision = trace.revision
        trace.start()
        unit_of_work.assistant.update_trace(trace, expected_revision=expected_revision)
        unit_of_work.commands.append_event(
            run_id=run.id,
            event_type="turn.trace.started",
            visibility=EventVisibility.USER,
            message="Assistant work trace started",
            payload={"trace_id": str(trace.id), "turn_id": str(trace.turn_id)},
            lease_owner=_lease_owner(run),
            lease_fence=_lease_fence(run),
        )
        return trace

    @staticmethod
    def _require_related_step(unit_of_work, trace: TurnTrace, step_id: UUID | None) -> None:
        if step_id is None:
            return
        step = unit_of_work.assistant.get_trace_step(step_id)
        if step is None or step.trace_id != trace.id or step.turn_id != trace.turn_id:
            raise InvalidTransitionError("Trace Step relationship crosses a Turn Trace")

    @staticmethod
    def _append_step_event(
        unit_of_work,
        *,
        run: CommandRun,
        step: TraceStep,
        event_type: str,
    ) -> None:
        unit_of_work.commands.append_event(
            run_id=run.id,
            event_type=f"turn.trace.step.{event_type}",
            visibility=EventVisibility(step.visibility.value),
            message=step.public_summary,
            payload={
                "trace_step_id": str(step.id),
                "trace_id": str(step.trace_id),
                "turn_id": str(step.turn_id),
                "sequence": step.sequence,
                "parent_step_id": str(step.parent_step_id) if step.parent_step_id else None,
                "caused_by_step_id": (
                    str(step.caused_by_step_id) if step.caused_by_step_id else None
                ),
                "kind": step.kind.value,
                "status": step.status.value,
                "public_summary": step.public_summary,
                "public_detail": step.public_detail,
                "model_role": step.model_role.value if step.model_role else None,
                "artifact_refs": [str(value) for value in step.artifact_refs],
                "duration_ms": step.duration_ms,
            },
            lease_owner=_lease_owner(run),
            lease_fence=_lease_fence(run),
        )


def _lease_owner(run: CommandRun) -> str | None:
    return run.lease_owner if run.status is CommandStatus.RUNNING else None


def _lease_fence(run: CommandRun) -> int | None:
    return run.lease_fence if run.status is CommandStatus.RUNNING else None


__all__ = ["TurnTraceRuntime"]
