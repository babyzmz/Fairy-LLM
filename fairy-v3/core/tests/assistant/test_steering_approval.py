from uuid import UUID

import pytest

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import RecordingToolExecutor, wait_for_turn
from tests.assistant.test_application import _scratch_task, _turn
from tests.assistant.test_approval_resume import _approval_provider


@pytest.mark.parametrize("approved", [False, True])
@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("extra_call", [False, True])
def test_steering_supersedes_unstarted_tool_without_reusing_approval(
    tmp_path, monkeypatch, approved, restart, extra_call,
):
    provider = _approval_provider(final_text="I will only explain, without sending a notification.")
    if extra_call:
        provider.rounds[0] = (
            provider.rounds[0][0],
            ModelDelta.tool_call(
                profile_id="scripted", sequence=2, tool_call_id="call-notify-later",
                tool_name="system.notify",
                arguments_fragment='{"title":"Later","body":"Second action","level":"info"}',
            ),
            ModelDelta.done(profile_id="scripted", sequence=3, finish_reason="tool_calls"),
        )
    executor = RecordingToolExecutor()
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((provider,)), tool_executor=executor,
        assistant_workflow_engine_version=4,
    )
    try:
        task = _scratch_task(service, "Notify me after approval")
        turn = _turn(service, task, "old-notification")
        other_task = _scratch_task(service, "Keep the other conversation untouched")
        other = _turn(service, other_task, "other-turn")
        assert service.invoke("assistant.turns.run", {"turn_id": turn["id"]})[
            "status"
        ] == "waiting_for_tool"
        approval = service.invoke("approvals.list", {"task_id": task["id"]})["items"][0]
        if approved:
            # Preserve the actual approval transaction but hold dispatch at its
            # normal queue boundary, allowing steering to arrive before execution.
            with monkeypatch.context() as patch:
                patch.setattr(service._assistant_scheduler, "start", lambda *a, **k: None)
                service.invoke("approvals.decide", {
                    "approval_id": approval["id"], "approved": True,
                })
        instruction = "Just explain what would happen. Do not send a notification."
        request = {
            "turn_id": turn["id"], "instruction": instruction,
            "expected_revision": 1, "idempotency_key": "steer:explain-only",
        }
        if restart:
            # Commit through the real ledger with dispatch stopped: exercise a
            # restart between plan supersession and the new model continuation.
            service._workflow_scheduler.close()
            service._assistant_scheduler._ledger.steer_turn(
                **{**request, "turn_id": UUID(turn["id"])},
            )
            service.close()
            service = build_local_service(
                tmp_path, provider_registry=ProviderRegistry((provider,)),
                tool_executor=executor, assistant_workflow_engine_version=4,
            )
        else:
            service.invoke("assistant.turns.steer", request)
        completed = wait_for_turn(service, turn["id"], timeout_seconds=5)
        assert completed["status"] == "completed"
        service.invoke("assistant.turns.steer", request)
        with service._unit_of_work_factory() as unit:
            invocations = unit.assistant.list_tool_invocations(UUID(turn["id"]))
            invocation = invocations[0]
            command = unit.commands.get_run(invocation.command_run_id)
            historical = unit.state.get_approval(UUID(approval["id"]))
            assert unit.assistant.get_turn(UUID(other["id"])).status == other["status"]
            assert unit.workflows.get_run(UUID(other["workflow_run_id"])).status == "paused"
            steps = unit.assistant.list_trace_steps(UUID(turn["id"]))
            workflow = unit.workflows.get(UUID(turn["workflow_run_id"]))
        assert workflow.run.active_plan_revision == 2
        assert len(workflow.instructions) == 1
        assert invocation.status == "rejected"
        assert invocation.error_code == "EXECUTION_INTENT_CHANGED"
        assert len(invocations) == (2 if extra_call else 1)
        assert all(item.status == "rejected" for item in invocations)
        assert command.status in {"cancelled", "rejected"}
        assert historical.decision == ("approved" if approved else "rejected")
        if not approved:
            assert historical.decided_by == "core:steering"
            with pytest.raises(InvalidTransitionError):
                service.invoke("approvals.decide", {
                    "approval_id": approval["id"], "approved": True,
                })
        else:
            service.invoke("approvals.decide", {
                "approval_id": approval["id"], "approved": True,
            })
        assert all(step.status not in {"pending", "running", "waiting"} for step in steps)
        assert not executor.calls
        assert len(provider.requests) == 2
        context = "\n".join(message.content for message in provider.requests[-1].messages)
        assert instruction in context
        result = next(message for message in provider.requests[-1].messages
                      if message.tool_call_id == "call-notify")
        assert "Not executed: superseded" in result.content
    finally:
        service.close()
