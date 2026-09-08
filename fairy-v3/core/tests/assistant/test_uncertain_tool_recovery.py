from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.providers import ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import RecordingToolExecutor, ScriptedProvider, wait_for_turn
from tests.assistant.test_application import _scratch_task, _turn
from tests.assistant.test_approval_resume import _approval_provider


@pytest.mark.parametrize("engine", [3, 4])
@pytest.mark.parametrize("effect_started", [False, True])
def test_restart_does_not_repeat_an_uncertain_effect_even_if_definition_is_idempotent(
    tmp_path,
    engine,
    effect_started,
):
    executor = RecordingToolExecutor()
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((ScriptedProvider(_approval_provider().rounds[:1]),)),
        tool_executor=executor,
        assistant_workflow_engine_version=engine,
    )
    try:
        task = _scratch_task(service, "Notify me after approval")
        turn = _turn(service, task, "uncertain-effect")
        service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        approval = service.invoke("approvals.list", {"task_id": task["id"]})["items"][0]
        service._application.record_approval_decision(
            approval_id=UUID(approval["id"]),
            approved=True,
            decided_by="user",
        )
        with service._unit_of_work_factory() as unit:
            invocation = unit.assistant.list_tool_invocations(UUID(turn["id"]))[0]
            command = unit.commands.claim(
                invocation.command_run_id,
                worker_id="crashed-tool-owner",
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
            )
            if effect_started:
                previous = invocation.status
                invocation.start()
                unit.assistant.update_tool_invocation(invocation, expected_status=previous)
            unit.commands.abandon(
                command.id,
                lease_owner=command.lease_owner,
                lease_fence=command.lease_fence,
            )
            scope = service._application.scope_for_task(
                unit.state, unit.state.get_task(UUID(task["id"]))
            )
            unit.commit()
        if effect_started:
            # External operation happened; no durable result was committed before the crash.
            executor.execute(
                service._registry.get(invocation.tool_name), scope, invocation.arguments
            )
    finally:
        service.close()
    provider = ScriptedProvider(_approval_provider().rounds[1:])
    restarted = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        tool_executor=executor,
        assistant_workflow_engine_version=engine,
    )
    try:
        outcome = wait_for_turn(
            restarted,
            turn["id"],
            status="failed" if effect_started else "completed",
        )
        assert len(executor.calls) == 1
        if effect_started:
            assert outcome["error_code"] == "TOOL_RESULT_UNCERTAIN"
            assert provider.requests == []
            with pytest.raises(InvalidTransitionError, match="actual result"):
                restarted.invoke("assistant.turns.retry", {
                    "turn_id": turn["id"], "idempotency_key": "unsafe-blind-retry",
                })
        else:
            assert len(provider.requests) == 1
    finally:
        restarted.close()
