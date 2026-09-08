from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import UUID

import pytest

from fairy_core.assistant.trace_models import TraceStepKind, TraceStepStatus
from fairy_core.commanding import CommandStatus
from fairy_core.commanding.registry import RiskLevel
from fairy_core.media.tools import MediaToolExecutor
from fairy_core.transports.stdio import build_local_service
from tests.assistant.test_application import _scratch_task, _turn
from tests.assistant.test_step_media import _create_media_turn
from tests.media import test_media_service as contracts


@pytest.mark.parametrize("takeover", [False, True])
def test_failure_projection_does_not_borrow_or_require_another_command_lease(tmp_path, takeover):
    service = build_local_service(tmp_path)
    bindings = []
    try:
        for index in range(2):
            task = _scratch_task(service, f"Independent trace {index}")
            turn = _turn(service, task, f"trace-authority:{index}")
            with service._unit_of_work_factory() as unit:
                scope = service._application.scope_for_task(
                    unit.state, unit.state.get_task(UUID(task["id"])),
                )
                run = unit.commands.create_run(
                    command_name="web.search", actor="assistant", scope=scope,
                    input_payload={}, risk_level=RiskLevel.LOW,
                    idempotency_key=f"trace-command:{index}",
                )
                unit.commands.transition(run.id, CommandStatus.QUEUED)
                run = unit.commands.claim(
                    run.id, worker_id="original-worker",
                    lease_until=datetime.now(UTC) + timedelta(minutes=1),
                )
                unit.commit()
            step = service._assistant_application._trace.append_step(
                turn_id=UUID(turn["id"]), run=run, kind=TraceStepKind.TOOL,
                status=TraceStepStatus.RUNNING, public_summary="Reading a public source",
            )
            bindings.append((turn, run, step))
        turn, original, step = bindings[0]
        with service._unit_of_work_factory() as unit:
            assert unit.commands.abandon(
                original.id, lease_owner=original.lease_owner, lease_fence=original.lease_fence,
            )
            if takeover:
                unit.commands.claim(
                    original.id, worker_id="new-owner",
                    lease_until=datetime.now(UTC) + timedelta(minutes=1),
                )
            expected = unit.commands.get_run(original.id)
            unit.commit()
        with service._unit_of_work_factory() as unit:
            service._assistant_application._trace.finish_active_steps_in_unit(
                unit, turn_id=UUID(turn["id"]), run=None,
                status=TraceStepStatus.FAILED, public_detail="Workflow could not finish",
            )
            unit.commit()
        with service._unit_of_work_factory() as unit:
            assert unit.commands.get_run(original.id) == expected
            assert unit.commands.get_run(bindings[1][1].id) == bindings[1][1]
            other_steps = unit.assistant.list_trace_steps(UUID(bindings[1][0]["id"]))
            assert all(item.status == "running" for item in other_steps)
            events = [event for event in unit.commands.events_after(cursor=0) if (
                event.event_type == "turn.trace.step.failed"
                and event.payload["trace_step_id"] == str(step.id)
            )]
            assert len(events) == 1
            assert events[0].run_id is None
            assert events[0].payload["command_run_id"] == str(original.id)
            assert events[0].conversation_id == UUID(turn["conversation_id"])
            assert events[0].task_id == UUID(turn["task_id"])
    finally:
        service.close()


def test_failed_domain_result_projection_settles_the_parent_without_replaying_media(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        contracts, "build_local_service",
        partial(contracts.build_local_service, assistant_workflow_engine_version=4),
    )
    original = MediaToolExecutor.read_command_result
    failures = []

    def broken(executor, scope, command):
        result = original(executor, scope, command)
        if result is not None and result.result is not None:
            failures.append(command.id)
            raise RuntimeError("Injected result projection failure")
        return result

    monkeypatch.setattr(MediaToolExecutor, "read_command_result", broken)
    service, coordinator, media, turn, task = _create_media_turn(tmp_path)
    try:
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        failed = service._workflow_scheduler.wait(UUID(turn["workflow_run_id"]), timeout=5)
        assert failed.run.status == "failed"
        with service._unit_of_work_factory() as unit:
            current = unit.assistant.get_turn(UUID(turn["id"]))
            assert current.status == "failed"
            assert current.error_code == failed.run.error_code == "WORKFLOW_NODE_FAILED"
            assert all(step.is_terminal for step in unit.assistant.list_trace_steps(current.id))
            (job,) = unit.state.list_media_jobs(UUID(task["id"]))
            assert job.status == "completed"
            assert unit.state.get_artifact(job.artifact_id) is not None
        assert failures == [job.command_run_id]
        assert len(media.image_requests) == 1
        assert len(coordinator.requests) == 2
    finally:
        service.close()
