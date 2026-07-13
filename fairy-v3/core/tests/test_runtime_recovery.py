from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

from fairy_core.application.runtime import (
    PreviewStartRequest,
    PreviewStopRequest,
    RuntimeApplication,
)
from fairy_core.commanding import CommandStatus, EventVisibility
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.commanding.schema import command_runs
from fairy_core.domain.execution import PreviewStatus, RuntimeStatus
from fairy_core.domain.models import TaskStatus
from fairy_core.runtime.models import ExecutorRuntimeState, RuntimeProbeResult
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


def test_startup_recovery_interrupts_a_ready_preview_missing_from_the_worker(
    tmp_path: Path,
) -> None:
    stack = build_runtime_stack(tmp_path)
    context = stack.runtime.start_preview(
        PreviewStartRequest(
            task_id=stack.task.task.id,
            idempotency_key="preview:startup-missing",
        )
    )
    stack.executor.probes.clear()

    recovered = stack.runtime.recover_interrupted(verify_running=True)

    assert recovered[-1].id == context.preview.id
    assert recovered[-1].status is PreviewStatus.INTERRUPTED
    with stack.factory() as unit_of_work:
        runtime = unit_of_work.state.get_runtime(context.runtime.id)
    assert runtime is not None and runtime.status is RuntimeStatus.INTERRUPTED


def test_startup_recovery_keeps_a_matching_ready_preview_live(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    context = stack.runtime.start_preview(
        PreviewStartRequest(
            task_id=stack.task.task.id,
            idempotency_key="preview:startup-live",
        )
    )

    recovered = stack.runtime.recover_interrupted(verify_running=True)

    assert recovered == ()
    assert stack.executor.probe_calls[-1] == context.runtime.executor_handle


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


def test_concurrent_start_replay_waits_for_one_external_dispatch(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    request = PreviewStartRequest(
        task_id=stack.task.task.id,
        idempotency_key="preview:concurrent-start",
    )
    entered = threading.Event()
    release = threading.Event()
    probe_attempted = threading.Event()
    original_start = stack.executor.start_static
    original_probe = stack.executor.probe

    def blocking_start(runtime_request):
        entered.set()
        assert release.wait(timeout=10)
        return original_start(runtime_request)

    def tracking_probe(executor_handle):
        probe_attempted.set()
        return original_probe(executor_handle)

    stack.executor.start_static = blocking_start  # type: ignore[method-assign]
    stack.executor.probe = tracking_probe  # type: ignore[method-assign]
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(stack.runtime.start_preview, request)
        assert entered.wait(timeout=10)
        second = executor.submit(stack.runtime.start_preview, request)
        assert not probe_attempted.wait(timeout=0.25)
        assert not second.done()
        release.set()
        contexts = (first.result(timeout=20), second.result(timeout=20))

    assert contexts[0].preview.id == contexts[1].preview.id
    assert contexts[0].preview.status is PreviewStatus.READY
    assert contexts[1].preview.status is PreviewStatus.READY
    assert len(stack.executor.start_calls) == 1


def test_peer_runtime_does_not_recover_another_live_lease(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    request = PreviewStartRequest(
        task_id=stack.task.task.id,
        idempotency_key="preview:peer-live-lease",
    )
    stack.runtime._prepare_start(request)
    registry = build_default_registry()
    peer = RuntimeApplication(
        unit_of_work_factory=stack.factory,
        executor=stack.executor,
        registry=registry,
        policy=PolicyEngine(registry),
        scope_resolver=stack.core.scope_for_task,
    )

    recovered = peer.recover_interrupted()

    assert recovered == ()
    assert stack.executor.probe_calls == []
    with stack.factory() as unit_of_work:
        runtime = unit_of_work.state.runtimes_for_task(stack.task.task.id)[-1]
    assert runtime.status is RuntimeStatus.STARTING


def test_recovery_rejects_probe_handle_rebinding(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    stack.executor.crash_after_start = True
    request = PreviewStartRequest(
        task_id=stack.task.task.id,
        idempotency_key="preview:forged-probe-handle",
    )
    with pytest.raises(SystemExit):
        stack.runtime.start_preview(request)

    expected_handle = next(iter(stack.executor.probes))
    current = stack.executor.probes[expected_handle]
    assert isinstance(current, RuntimeProbeResult)
    stack.executor.probes[expected_handle] = RuntimeProbeResult(
        executor_handle="static:0198f4de-0114-7000-8000-000000000099",
        state=ExecutorRuntimeState.RUNNING,
        host=current.host,
        port=current.port,
        url=current.url,
    )

    recovered = stack.runtime.recover_interrupted()

    assert recovered[-1].status is PreviewStatus.INTERRUPTED
    with stack.factory() as unit_of_work:
        runtime = unit_of_work.state.runtimes_for_task(stack.task.task.id)[-1]
    assert runtime.status is RuntimeStatus.INTERRUPTED
