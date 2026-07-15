from __future__ import annotations

from uuid import UUID

import pytest

from fairy_core.assistant.trace_models import (
    TraceStep,
    TraceStepKind,
    TraceStepStatus,
    TurnTrace,
)
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.providers import ModelExecutionRole


def test_turn_trace_lifecycle_is_monotonic() -> None:
    trace = TurnTrace.create(
        turn_id=UUID(int=1),
        conversation_id=UUID(int=2),
        task_id=UUID(int=3),
    )

    trace.start()
    started_revision = trace.revision
    trace.start()
    trace.complete()

    assert started_revision == 1
    assert trace.revision == 2
    assert trace.started_at is not None
    assert trace.completed_at is not None
    with pytest.raises(InvalidTransitionError, match="restart"):
        trace.start()


def test_trace_step_transitions_and_provider_attempt_binding() -> None:
    step = TraceStep.create(
        trace_id=UUID(int=1),
        turn_id=UUID(int=2),
        sequence=1,
        kind=TraceStepKind.MODEL,
        status=TraceStepStatus.RUNNING,
        public_summary="Kimi is implementing the requested change",
        model_id="moonshotai/kimi-k2.7-code",
        model_role=ModelExecutionRole.PRIMARY,
        command_run_id=UUID(int=3),
    )

    step.bind_provider_attempt(UUID(int=4))
    step.transition(
        TraceStepStatus.SUCCEEDED,
        public_detail="Implementation draft produced",
    )

    assert step.provider_attempt_id == UUID(int=4)
    assert step.is_terminal is True
    assert step.duration_ms is not None
    assert step.revision == 2
    with pytest.raises(InvalidTransitionError, match="terminal"):
        step.bind_provider_attempt(UUID(int=5))
    with pytest.raises(InvalidTransitionError, match="cannot transition"):
        step.transition(TraceStepStatus.RUNNING)


def test_trace_step_rejects_partial_model_identity_and_oversized_public_text() -> None:
    with pytest.raises(ValueError, match="model id and role"):
        TraceStep.create(
            trace_id=UUID(int=1),
            turn_id=UUID(int=2),
            sequence=1,
            kind=TraceStepKind.MODEL,
            status=TraceStepStatus.PENDING,
            public_summary="Model queued",
            model_id="model-without-role",
        )
    with pytest.raises(ValueError, match="public_summary"):
        TraceStep.create(
            trace_id=UUID(int=1),
            turn_id=UUID(int=2),
            sequence=1,
            kind=TraceStepKind.REASONING,
            status=TraceStepStatus.SUCCEEDED,
            public_summary="x" * 513,
        )
