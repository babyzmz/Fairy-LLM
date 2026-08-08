from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import UUID

import pytest

from fairy_core.assistant.models import ToolInvocationStatus
from fairy_core.commanding.models import CommandStatus
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.models import TaskStatus
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import RecordingToolExecutor, ScriptedProvider, wait_for_turn
from tests.assistant.test_application import _scratch_task, _turn


def _approval_provider(*, final_text: str = "Action handled") -> ScriptedProvider:
    return ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="call-notify",
                    tool_name="system.notify",
                    arguments_fragment=('{"title":"Fairy","body":"Task finished","level":"info"}'),
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="tool_calls",
                ),
            ),
            (
                ModelDelta.text(
                    profile_id="scripted",
                    sequence=1,
                    text=final_text,
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ]
    )


def test_standard_profile_approval_resumes_one_tool_effect_once(tmp_path: Path) -> None:
    provider = _approval_provider()
    executor = RecordingToolExecutor(summary="Notification sent")
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        tool_executor=executor,
    )
    try:
        task = _scratch_task(service, "Notify me when this is complete")
        turn = _turn(service, task, "turn:approval:resume")

        waiting = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        approval = service.invoke(
            "approvals.list",
            {"task_id": task["id"]},
        )["items"][0]

        assert waiting["status"] == "waiting_for_tool"
        assert approval["changeset_id"] is None
        assert approval["tool_invocation_id"] is not None
        assert executor.calls == []
        assert "system.notify" in {tool.name for tool in provider.requests[0].tools}

        decision = service.invoke(
            "approvals.decide",
            {
                "approval_id": approval["id"],
                "approved": True,
            },
        )
        assert decision["approval"]["decision"] == "approved"
        assert decision["changeset"] is None
        assert decision["assistant_turn_id"] == turn["id"]
        assert decision["resume_requested"] is True

        completed = wait_for_turn(service, turn["id"])
        replayed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        trace = service.invoke("assistant.turns.trace.list", {"turn_id": turn["id"]})

        assert completed["status"] == "completed"
        assert replayed == completed
        assert len(executor.calls) == 1
        assert len(provider.requests) == 2
        protocol = provider.requests[1].messages[-2:]
        assert protocol[0].tool_calls[0].id == "call-notify"
        assert protocol[1].tool_call_id == "call-notify"
        assert "Notification sent" in protocol[1].content
        assert [step["kind"] for step in trace["steps"]] == [
            "model",
            "reasoning",
            "tool",
            "approval",
            "observation",
            "model",
            "response",
        ]
        assert all(step["status"] == "succeeded" for step in trace["steps"])
    finally:
        service.close()


def test_rejected_tool_becomes_bounded_result_and_duplicate_decision_is_idempotent(
    tmp_path: Path,
) -> None:
    provider = _approval_provider(final_text="Notification was not sent")
    executor = RecordingToolExecutor()
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        tool_executor=executor,
    )
    try:
        task = _scratch_task(service, "Notify me when complete, but ask before sending it")
        turn = _turn(service, task, "turn:approval:reject")
        service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        approval = service.invoke("approvals.list", {"task_id": task["id"]})["items"][0]
        request = {
            "approval_id": approval["id"],
            "approved": False,
        }

        first = service.invoke("approvals.decide", request)
        second = service.invoke("approvals.decide", request)
        completed = wait_for_turn(service, turn["id"])
        trace = service.invoke("assistant.turns.trace.list", {"turn_id": turn["id"]})

        assert first == second
        assert first["assistant_turn_id"] == turn["id"]
        assert first["resume_requested"] is True
        assert completed["status"] == "completed"
        assert executor.calls == []
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            invocation = unit_of_work.assistant.list_tool_invocations(turn["id"])[0]
            command = unit_of_work.commands.get_run(invocation.command_run_id)
            persisted_task = unit_of_work.state.get_task(task["id"])
        assert invocation.status is ToolInvocationStatus.REJECTED
        assert command is not None and command.status is CommandStatus.REJECTED
        assert persisted_task is not None and persisted_task.status is TaskStatus.READY
        assert "rejected" in provider.requests[1].messages[-1].content.lower()
        tool_step = next(step for step in trace["steps"] if step["kind"] == "tool")
        approval_step = next(step for step in trace["steps"] if step["kind"] == "approval")
        assert tool_step["status"] == "cancelled"
        assert approval_step["status"] == "cancelled"
        assert trace["completed_at"] is not None
        assert all(
            step["status"] not in {"pending", "running", "waiting"} for step in trace["steps"]
        )

        with pytest.raises(InvalidTransitionError):
            service.invoke(
                "approvals.decide",
                {**request, "approved": True},
            )
    finally:
        service.close()


def test_approval_queued_before_prior_background_runner_exits_resumes_once(
    tmp_path: Path,
) -> None:
    provider = _approval_provider()
    executor = RecordingToolExecutor(summary="Notification sent once")
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        tool_executor=executor,
    )
    execution_finish_reached = Event()
    release_execution_finish = Event()
    finish_calls = 0
    scheduler = service._workflow_scheduler  # type: ignore[attr-defined]
    original_finish = scheduler._finish_active  # type: ignore[attr-defined]

    def delayed_first_finish(active) -> None:
        nonlocal finish_calls
        finish_calls += 1
        # The durable interpretation node now completes before the execution node.
        if finish_calls == 2:
            execution_finish_reached.set()
            assert release_execution_finish.wait(timeout=5)
        original_finish(active)

    scheduler._finish_active = delayed_first_finish  # type: ignore[attr-defined,method-assign]
    try:
        task = _scratch_task(
            service,
            "Notify me after approval while the first Runner is exiting",
        )
        turn = _turn(service, task, "turn:approval:runner-race")
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        assert execution_finish_reached.wait(timeout=5)
        approval = service.invoke("approvals.list", {"task_id": task["id"]})["items"][0]

        service.invoke(
            "approvals.decide",
            {"approval_id": approval["id"], "approved": True},
        )
        release_execution_finish.set()
        completed = wait_for_turn(service, turn["id"])

        assert completed["status"] == "completed"
        assert len(executor.calls) == 1
        assert len(provider.requests) == 2
    finally:
        release_execution_finish.set()
        service.close()


def test_approved_turn_resumes_after_core_restart_and_expired_claim(
    tmp_path: Path,
) -> None:
    first_provider = ScriptedProvider(_approval_provider().rounds[:1])
    first_service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((first_provider,)),
        tool_executor=RecordingToolExecutor(),
    )
    task = _scratch_task(first_service, "Resume this approved notification action")
    turn = _turn(first_service, task, "turn:approval:restart")
    first_service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
    approval = first_service.invoke("approvals.list", {"task_id": task["id"]})["items"][0]
    first_service._application.record_approval_decision(  # type: ignore[attr-defined]
        approval_id=UUID(approval["id"]),
        approved=True,
        decided_by="user",
    )
    with first_service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
        invocation = unit_of_work.assistant.list_tool_invocations(UUID(turn["id"]))[0]
        assert invocation.command_run_id is not None
        unit_of_work.commands.claim(
            invocation.command_run_id,
            worker_id="crashed-core",
            lease_until=datetime.now(UTC) + timedelta(milliseconds=20),
        )
        unit_of_work.commit()
    first_service.close()
    time.sleep(0.05)

    second_provider = ScriptedProvider(
        [
            (
                ModelDelta.text(
                    profile_id="scripted",
                    sequence=1,
                    text="Recovered once",
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            )
        ]
    )
    executor = RecordingToolExecutor(summary="Recovered effect")
    second_service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((second_provider,)),
        tool_executor=executor,
    )
    try:
        completed = wait_for_turn(second_service, turn["id"])

        assert completed["status"] == "completed"
        assert len(executor.calls) == 1
        assert second_provider.requests[0].messages[-1].tool_call_id == "call-notify"
    finally:
        second_service.close()
