from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

from fairy_core.application.runtime import (
    PreviewStartRequest,
    PreviewStopRequest,
)
from fairy_core.commanding import CommandStatus, EventVisibility
from fairy_core.commanding.schema import command_runs
from fairy_core.domain.execution import PreviewStatus, RuntimeStatus
from fairy_core.domain.models import TaskStatus
from tests.runtime_support import build_runtime_stack


def test_recovery_marks_intent_without_external_runtime_interrupted(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    stack.executor.crash_after_start = True
    stack.executor.probes.clear()

    with pytest.raises(SystemExit):
        stack.runtime.start_preview(
            PreviewStartRequest(
                task_id=stack.task.task.id,
                idempotency_key="preview:crash-before-result",
            )
        )
    stack.executor.probes.clear()

    recovered = stack.runtime.recover_interrupted()

    assert recovered[-1].status is PreviewStatus.INTERRUPTED
    with stack.factory() as unit_of_work:
        runtime = unit_of_work.state.runtimes_for_task(stack.task.task.id)[-1]
        task = unit_of_work.state.get_task(stack.task.task.id)
    assert runtime.status is RuntimeStatus.INTERRUPTED
    assert task is not None and task.status is TaskStatus.FAILED


def test_recovery_completes_crash_after_external_start_from_probe(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    stack.executor.crash_after_start = True

    with pytest.raises(SystemExit):
        stack.runtime.start_preview(
            PreviewStartRequest(
                task_id=stack.task.task.id,
                idempotency_key="preview:crash-after-start",
            )
        )

    recovered = stack.runtime.recover_interrupted()
    second_pass = stack.runtime.recover_interrupted()

    assert recovered[-1].status is PreviewStatus.READY
    assert second_pass == ()
    assert len(stack.executor.start_calls) == 1
    with stack.factory() as unit_of_work:
        runtime = unit_of_work.state.runtimes_for_task(stack.task.task.id)[-1]
        task = unit_of_work.state.get_task(stack.task.task.id)
    assert runtime.status is RuntimeStatus.RUNNING
    assert task is not None and task.status is TaskStatus.PREVIEWING


def test_start_replay_recovers_pending_intent_without_redispatch(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    stack.executor.crash_after_start = True
    request = PreviewStartRequest(
        task_id=stack.task.task.id,
        idempotency_key="preview:start-replay-recovery",
    )

    with pytest.raises(SystemExit):
        stack.runtime.start_preview(request)
    stack.executor.crash_after_start = False

    recovered = stack.runtime.start_preview(request)

    assert recovered.preview.status is PreviewStatus.READY
    assert recovered.runtime.status is RuntimeStatus.RUNNING
    assert len(stack.executor.start_calls) == 1


def test_recovery_reclaims_and_commits_expired_command_lease(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    stack.executor.crash_after_start = True

    with pytest.raises(SystemExit):
        stack.runtime.start_preview(
            PreviewStartRequest(
                task_id=stack.task.task.id,
                idempotency_key="preview:expired-lease",
            )
        )
    with stack.engine.begin() as connection:
        connection.execute(
            update(command_runs)
            .where(
                command_runs.c.task_id == str(stack.task.task.id),
                command_runs.c.command_name == "preview.start",
                command_runs.c.status == CommandStatus.RUNNING.value,
            )
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )

    recovered = stack.runtime.recover_interrupted()

    assert recovered[-1].status is PreviewStatus.READY
    with stack.factory() as unit_of_work:
        events = unit_of_work.commands.events_after(
            cursor=0,
            allowed_visibilities={EventVisibility.USER},
        )
    assert "command.reclaimed" in {event.event_type for event in events}


def test_recovery_finishes_stop_after_external_stop_crash(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    context = stack.runtime.start_preview(
        PreviewStartRequest(
            task_id=stack.task.task.id,
            idempotency_key="preview:stop-recovery",
        )
    )
    stack.executor.crash_after_stop = True

    with pytest.raises(SystemExit):
        stack.runtime.stop_preview(
            PreviewStopRequest(
                preview_id=context.preview.id,
                idempotency_key="preview:stop-recovery:stop",
            )
        )

    recovered = stack.runtime.recover_interrupted()

    assert recovered[-1].status is PreviewStatus.STOPPED
    with stack.factory() as unit_of_work:
        runtime = unit_of_work.state.get_runtime(context.runtime.id)
    assert runtime is not None and runtime.status is RuntimeStatus.STOPPED


def test_stop_replay_recovers_pending_stop_without_second_dispatch(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    context = stack.runtime.start_preview(
        PreviewStartRequest(
            task_id=stack.task.task.id,
            idempotency_key="preview:stop-replay",
        )
    )
    request = PreviewStopRequest(
        preview_id=context.preview.id,
        idempotency_key="preview:stop-replay:stop",
    )
    stack.executor.crash_after_stop = True

    with pytest.raises(SystemExit):
        stack.runtime.stop_preview(request)
    stack.executor.crash_after_stop = False

    stopped = stack.runtime.stop_preview(request)

    assert stopped.status is PreviewStatus.STOPPED
    assert len(stack.executor.stop_calls) == 1
